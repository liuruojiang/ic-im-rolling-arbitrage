from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import research_v80_a_ic_im_v2_50_50 as base


ROOT = Path(__file__).resolve().parent
MOMENTUM_ROOT = ROOT.parent / "A股美股动量组合策略"
DIVIDEND_ROOT = Path(r"C:\Users\Administrator.DESKTOP-95I7VVU\Documents\Codex\2026-06-16\100")
BASE_OUTPUT = ROOT / "outputs" / "a_v80_ic_im_v2_50_50_20260901"
BASE_DAILY = BASE_OUTPUT / "daily_returns.csv"
BASE_AUDIT = BASE_OUTPUT / "audit.json"
BASE_AMOUNT = BASE_OUTPUT / "v80_amount_overlay_evidence.csv"
V80_SOURCE = MOMENTUM_ROOT / "mnt_bot V 8.0 plus.py"
V80_PANEL = (
    MOMENTUM_ROOT
    / "outputs"
    / "v78_v79_vol_management_and_sleeve_diversification_20260824"
    / "latest_market_data"
    / "cn_close.csv"
)
ETF_RAW = DIVIDEND_ROOT / "work" / "515100_sina_raw_daily_to_20260825.csv"
ETF_DIVIDENDS = DIVIDEND_ROOT / "work" / "515100_dividend_eastmoney.csv"
ETF_YAHOO_ADJUSTED = DIVIDEND_ROOT / "work" / "515100_combined_adjusted_daily_to_20260824.csv"
OUTPUT = ROOT / "outputs" / "a_v80_ic_im_v2_hldw100_etf20_20260901"

ETF_CODE = "515100"
ETF_NAME = "红利低波100ETF"
ETF_UNDERLYING = "中证红利低波100（930955）"

WEIGHTS = {
    "Baseline_A50_IC25_IM25": {"V80_A": 0.50, "IC_V2": 0.25, "IM_V2": 0.25, "HLDW100_ETF": 0.00},
    "Main_A40_IC20_IM20_ETF20": {"V80_A": 0.40, "IC_V2": 0.20, "IM_V2": 0.20, "HLDW100_ETF": 0.20},
    "ETF20_funded_from_A": {"V80_A": 0.30, "IC_V2": 0.25, "IM_V2": 0.25, "HLDW100_ETF": 0.20},
    "ETF20_funded_from_ICIM": {"V80_A": 0.50, "IC_V2": 0.15, "IM_V2": 0.15, "HLDW100_ETF": 0.20},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head(path: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def load_v80_module():
    spec = importlib.util.spec_from_file_location("v80_hldw100_etf20", V80_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load V8.0 authority: {V80_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_saved_feature(path: Path) -> tuple[pd.Series, pd.DataFrame]:
    feature = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    for column in feature.select_dtypes(include="object"):
        values = set(feature[column].dropna().astype(str).str.lower().unique())
        if values <= {"true", "false"}:
            feature[column] = feature[column].astype(str).str.lower().eq("true")
    signal = feature.pop("combined_signal_used").fillna(False).astype(bool)
    return signal, feature


def build_etf_total_return() -> tuple[pd.DataFrame, dict[str, object]]:
    raw = pd.read_csv(ETF_RAW, parse_dates=["date"]).sort_values("date")
    if raw["date"].duplicated().any():
        raise RuntimeError("515100 raw close has duplicate dates")
    raw = raw.set_index("date")
    close = pd.to_numeric(raw["close"], errors="coerce").dropna().astype(float)
    if (close <= 0).any():
        raise RuntimeError("515100 raw close contains non-positive values")

    dividends = pd.read_csv(
        ETF_DIVIDENDS,
        parse_dates=["record_date", "ex_date", "pay_date"],
    ).sort_values("ex_date")
    if dividends["ex_date"].duplicated().any():
        raise RuntimeError("515100 dividend table has duplicate ex-dates")
    missing_ex_dates = sorted(set(dividends["ex_date"]) - set(close.index))
    if missing_ex_dates:
        raise RuntimeError(f"515100 dividend ex-dates missing from close history: {missing_ex_dates}")

    total_return = close.pct_change(fill_method=None)
    previous_close = close.shift(1)
    dividend_by_date = dividends.set_index("ex_date")["cash_per_share"].astype(float)
    total_return.loc[dividend_by_date.index] = (
        close.loc[dividend_by_date.index] + dividend_by_date
    ) / previous_close.loc[dividend_by_date.index] - 1.0
    total_nav = (1.0 + total_return.dropna()).cumprod()

    output = pd.DataFrame(index=close.index)
    output["raw_close"] = close
    output["cash_dividend"] = dividend_by_date.reindex(output.index).fillna(0.0)
    output["total_return"] = total_return
    output["total_return_nav"] = total_nav.reindex(output.index)

    yahoo = pd.read_csv(ETF_YAHOO_ADJUSTED, parse_dates=["date"]).set_index("date").sort_index()
    yahoo_adj = pd.to_numeric(yahoo["adj_close"], errors="coerce").dropna()
    yahoo_ret = yahoo_adj.pct_change(fill_method=None).rename("yahoo_adjusted_return")
    # Yahoo occasionally omits an otherwise valid Sina trading day.  In that
    # case its next pct_change spans two sessions and is not comparable with
    # the one-session explicit return.  Retain only identical date pairs.
    raw_previous_date = pd.Series(close.index, index=close.index).shift(1)
    yahoo_previous_date = pd.Series(yahoo_adj.index, index=yahoo_adj.index).shift(1)
    comparable_dates = yahoo_previous_date.index[
        yahoo_previous_date.eq(raw_previous_date.reindex(yahoo_previous_date.index))
    ]
    crosscheck = pd.concat(
        [total_return.rename("explicit_cash_dividend_return"), yahoo_ret], axis=1
    ).loc[comparable_dates].dropna()
    crosscheck["abs_diff"] = (
        crosscheck["explicit_cash_dividend_return"] - crosscheck["yahoo_adjusted_return"]
    ).abs()
    crosscheck.to_csv(OUTPUT / "etf_adjustment_crosscheck.csv", encoding="utf-8-sig", index_label="date")
    cumulative = (1.0 + crosscheck.iloc[:, :2]).prod()
    evidence = {
        "raw_start": close.index.min().date().isoformat(),
        "raw_end": close.index.max().date().isoformat(),
        "raw_rows": int(len(close)),
        "dividend_events": dividends[
            ["record_date", "ex_date", "cash_per_share", "pay_date"]
        ].astype({"cash_per_share": float}).to_dict(orient="records"),
        "adjustment_method": "raw close plus cash distribution on ex-date; dividend-reinvested economic total return",
        "yahoo_crosscheck_rows": int(len(crosscheck)),
        "yahoo_crosscheck_median_abs_daily_return_diff": float(crosscheck["abs_diff"].median()),
        "yahoo_crosscheck_max_abs_daily_return_diff": float(crosscheck["abs_diff"].max()),
        "explicit_cumulative_crosscheck": float(cumulative.iloc[0]),
        "yahoo_cumulative_crosscheck": float(cumulative.iloc[1]),
    }
    return output, evidence


def metric_lookup(metrics: pd.DataFrame, series: str, window: str = "Full") -> pd.Series:
    rows = metrics[metrics["series"].eq(series) & metrics["window"].eq(window)]
    if len(rows) != 1:
        raise RuntimeError(f"Metric row not unique: {series} / {window}")
    return rows.iloc[0]


def fmt_pct(value: object) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.2%}"


def fmt_num(value: object) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.3f}"


def make_plot(returns: pd.DataFrame, path: Path) -> None:
    labels = {
        "Baseline_A50_IC25_IM25": "Baseline A50/IC25/IM25",
        "Main_A40_IC20_IM20_ETF20": "A40/IC20/IM20/ETF20",
        "HLDW100_ETF": "515100 total return",
    }
    selected = returns[list(labels)]
    nav = (1.0 + selected).cumprod()
    dd = nav / nav.cummax() - 1.0
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for column, label in labels.items():
        axes[0].plot(nav.index, nav[column], label=label, linewidth=1.7)
        axes[1].plot(dd.index, dd[column], label=label, linewidth=1.3)
    axes[0].set_ylabel("NAV (rebased to 1)")
    axes[1].set_ylabel("Drawdown")
    axes[1].yaxis.set_major_formatter(lambda value, _pos: f"{value:.0%}")
    for axis in axes:
        axis.grid(alpha=0.25)
    axes[0].legend(loc="upper left")
    fig.suptitle("20% CSI Dividend Low Volatility 100 ETF allocation")
    fig.tight_layout()
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def build_report(metrics: pd.DataFrame, audit: dict[str, object]) -> str:
    labels = {
        "Baseline_A50_IC25_IM25": "基线：A50 / IC25 / IM25",
        "Main_A40_IC20_IM20_ETF20": "主方案：A40 / IC20 / IM20 / ETF20",
        "ETF20_funded_from_A": "ETF 20%全部从A划出",
        "ETF20_funded_from_ICIM": "ETF 20%全部从IC/IM划出",
        "Main_monthly_rebalance": "主方案月度再平衡",
        "HLDW100_ETF": "515100单独",
    }
    order = list(labels)
    table = [
        "| 方案 | 窗口 | 状态 | CAGR | 年化波动 | Sharpe | MaxDD |",
        "|:--|:--|:--|--:|--:|--:|--:|",
    ]
    for name in order:
        for window in ("Full", "10Y", "5Y", "3Y", "1Y"):
            row = metric_lookup(metrics, name, window)
            table.append(
                f"| {labels[name]} | {window} | {row['status']} | {fmt_pct(row['cagr_calendar'])} | "
                f"{fmt_pct(row['annualized_volatility_252'])} | {fmt_num(row['sharpe_repo_252'])} | "
                f"{fmt_pct(row['max_drawdown'])} |"
            )

    baseline = metric_lookup(metrics, "Baseline_A50_IC25_IM25")
    main = metric_lookup(metrics, "Main_A40_IC20_IM20_ETF20")
    etf = metric_lookup(metrics, "HLDW100_ETF")
    monthly = metric_lookup(metrics, "Main_monthly_rebalance")
    delta_cagr = float(main["cagr_calendar"] - baseline["cagr_calendar"])
    delta_vol = float(main["annualized_volatility_252"] - baseline["annualized_volatility_252"])
    delta_dd = float(main["max_drawdown"] - baseline["max_drawdown"])
    overlap = audit["hldw100_overlap"]
    cross = audit["etf_data_evidence"]
    lines = [
        "# 20% 红利低波100ETF 配置研究",
        "",
        "## 结论",
        "",
        f"- 主方案为 **A 40% / IC 20% / IM 20% / 515100 20%**。共同正式区间 "
        f"`{audit['common_start']}` 至 `{audit['common_end']}`，Full CAGR **{fmt_pct(main['cagr_calendar'])}**，"
        f"MaxDD **{fmt_pct(main['max_drawdown'])}**，年化波动 **{fmt_pct(main['annualized_volatility_252'])}**，"
        f"Sharpe **{fmt_num(main['sharpe_repo_252'])}**。",
        f"- 相对原基线，CAGR变化 **{delta_cagr:+.2%}**，波动变化 **{delta_vol:+.2%}**，"
        f"最大回撤变化 **{delta_dd:+.2%}**（正数表示回撤变浅）。这次20%配置主要降低波动，"
        "没有带来明显回撤改善，并显著压低历史收益。",
        f"- 515100 在同区间单独 CAGR/MaxDD 为 {fmt_pct(etf['cagr_calendar'])} / "
        f"{fmt_pct(etf['max_drawdown'])}，明显弱于原三策略组合。",
        f"- 月度再平衡敏感性为 CAGR {fmt_pct(monthly['cagr_calendar'])}、MaxDD "
        f"{fmt_pct(monthly['max_drawdown'])}，结论不依赖每日固定权重假设。",
        "- 无论20%从A、从IC/IM或按比例划出，Full结果差别很小；主要结论来自515100本身在该正式区间的收益较低。",
        "",
        "## 标准窗口结果",
        "",
        *table,
        "",
        "## 重复暴露",
        "",
        f"V8.0 A 本身已有中证红利低波100作为候选资产；共同样本中实际持有该指数 "
        f"{overlap['a_hldw_holding_days']} 天（{overlap['a_hldw_holding_day_share']:.2%}）。",
        f"在主方案中，固定ETF使红利低波100相关敞口最低为20%；计入A腿目标波动率后，"
        f"平均约 {overlap['main_average_effective_hldw_exposure']:.2%}，最高约 "
        f"{overlap['main_max_effective_hldw_exposure']:.2%}。这是结构性风格倾斜，不是独立的新风险来源。",
        "",
        "## 数据与执行口径",
        "",
        f"- ETF：上交所 `{ETF_CODE}`，跟踪{ETF_UNDERLYING}。使用Sina真实未复权收盘价，"
        "在除息日把每份现金分红计入经济总回报；基金费率和跟踪误差已反映在市场价格中。",
        f"- 复权交叉验证：与Yahoo adjusted close重叠 {cross['yahoo_crosscheck_rows']} 行，"
        f"日收益绝对差中位数 {cross['yahoo_crosscheck_median_abs_daily_return_diff']:.3e}，"
        f"累计倍数 {cross['explicit_cumulative_crosscheck']:.6f} vs "
        f"{cross['yahoo_cumulative_crosscheck']:.6f}。",
        "- 原 A/IC/IM 日收益、内部成本、执行时点和30%期货保证金/缓冲口径全部保留。"
        "ETF按被动持有处理；没有另扣首次建仓及跨袖套再平衡佣金。",
        "- 日度固定权重为主测试，另给月度再平衡敏感性；没有前向填充缺失交易日。",
        "",
        "## 完整性与边界",
        "",
        f"- V8.0 A 状态链复算与上一轮正式日收益最大误差 `{audit['v80_a_return_parity_max_abs']:.3e}`。",
        f"- 共同日期 {audit['common_rows']} 行；ETF缺失日期 {audit['etf_missing_dates']}。",
        "- 本结果是研究审计，不是实盘授权或调仓建议。正式样本不足四年，且未模拟ETF盘口冲击、"
        "涨跌停、申赎折溢价、跨策略资金划转及账户级保证金压力。",
        "",
        "详细产物：`daily_returns.csv`、`window_metrics.csv`、`correlations.csv`、"
        "`a_hldw_overlap.csv`、`etf_total_return.csv`、`audit.json`、`nav_drawdown.png`。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    required = [
        BASE_DAILY,
        BASE_AUDIT,
        BASE_AMOUNT,
        V80_SOURCE,
        V80_PANEL,
        ETF_RAW,
        ETF_DIVIDENDS,
        ETF_YAHOO_ADJUSTED,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required files: {missing}")
    OUTPUT.mkdir(parents=True, exist_ok=True)

    base_audit = json.loads(BASE_AUDIT.read_text(encoding="utf-8"))
    base_daily = pd.read_csv(BASE_DAILY, parse_dates=["date"]).set_index("date").sort_index()
    component = base_daily[["V80_A", "IC_V2", "IM_V2"]].astype(float)
    formal_end = component.index.max()
    if formal_end.date().isoformat() != base_audit["common_end"]:
        raise RuntimeError("Base daily return end does not match its audit")

    etf_path, etf_evidence = build_etf_total_return()
    etf_return = etf_path["total_return"].reindex(component.index)
    etf_missing = int(etf_return.isna().sum())
    if etf_missing:
        raise RuntimeError(f"515100 has {etf_missing} missing dates in the formal common sample")
    component["HLDW100_ETF"] = etf_return

    returns = component.copy()
    for name, weight_map in WEIGHTS.items():
        weights = pd.Series(weight_map, dtype=float)
        if not np.isclose(weights.sum(), 1.0):
            raise RuntimeError(f"Weights do not sum to one: {name}")
        returns[name] = component.mul(weights).sum(axis=1)
    main_weights = pd.Series(WEIGHTS["Main_A40_IC20_IM20_ETF20"], dtype=float)
    returns["Main_monthly_rebalance"] = base.monthly_rebalanced_return(
        component, main_weights
    ).rename("Main_monthly_rebalance")

    v80 = load_v80_module()
    cn_close = pd.read_csv(V80_PANEL, parse_dates=["date"]).set_index("date").sort_index().loc[:formal_end]
    volume_signal, volume_feature = parse_saved_feature(BASE_AMOUNT)
    a_state = v80.run_cn_strategy(cn_close, v80.CN_EQUITY_CODES)
    a_state = v80.apply_suba_volume_overlay(
        a_state,
        cn_close,
        volume_signal,
        volume_feature,
        scale=v80.CN_SA_VOLUME_SCALE,
        rule_name=v80.CN_SA_VOLUME_RULE_NAME,
    )
    a_aligned = a_state.reindex(component.index)
    if a_aligned["return"].isna().any():
        raise RuntimeError("V8.0 A state is missing formal common dates")
    a_parity = float((a_aligned["return"] - component["V80_A"]).abs().max())
    if a_parity > 1e-12:
        raise RuntimeError(f"V8.0 A parity failed: {a_parity}")

    a_hldw_weight = pd.to_numeric(a_aligned["weight"], errors="coerce").fillna(0.0).where(
        a_aligned["effective_holding"].eq("1.930955"), 0.0
    )
    overlap = pd.DataFrame(index=component.index)
    overlap["a_effective_holding"] = a_aligned["effective_holding"]
    overlap["a_effective_weight"] = pd.to_numeric(a_aligned["weight"], errors="coerce")
    overlap["a_hldw_effective_weight"] = a_hldw_weight
    overlap["baseline_effective_hldw_exposure"] = 0.50 * a_hldw_weight
    overlap["main_effective_hldw_exposure"] = 0.20 + 0.40 * a_hldw_weight
    overlap_stats = {
        "a_hldw_holding_days": int(a_hldw_weight.gt(0).sum()),
        "a_hldw_holding_day_share": float(a_hldw_weight.gt(0).mean()),
        "a_average_hldw_weight_when_held": float(a_hldw_weight[a_hldw_weight.gt(0)].mean()),
        "a_max_hldw_weight": float(a_hldw_weight.max()),
        "baseline_average_effective_hldw_exposure": float(overlap["baseline_effective_hldw_exposure"].mean()),
        "baseline_max_effective_hldw_exposure": float(overlap["baseline_effective_hldw_exposure"].max()),
        "main_average_effective_hldw_exposure": float(overlap["main_effective_hldw_exposure"].mean()),
        "main_max_effective_hldw_exposure": float(overlap["main_effective_hldw_exposure"].max()),
    }

    metric_series = {
        "Baseline_A50_IC25_IM25": returns["Baseline_A50_IC25_IM25"],
        "Main_A40_IC20_IM20_ETF20": returns["Main_A40_IC20_IM20_ETF20"],
        "ETF20_funded_from_A": returns["ETF20_funded_from_A"],
        "ETF20_funded_from_ICIM": returns["ETF20_funded_from_ICIM"],
        "Main_monthly_rebalance": returns["Main_monthly_rebalance"],
        "HLDW100_ETF": returns["HLDW100_ETF"],
    }
    metrics = base.standard_metrics(metric_series, formal_end)
    correlations = component.corr()

    audit = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "live_approved": False,
        "interpretation": "20% ETF funded by proportional 0.8 scaling of prior A50/IC25/IM25 portfolio",
        "weights": WEIGHTS,
        "common_start": component.index.min().date().isoformat(),
        "common_end": component.index.max().date().isoformat(),
        "common_rows": int(len(component)),
        "etf_missing_dates": etf_missing,
        "etf": {"code": ETF_CODE, "name": ETF_NAME, "underlying": ETF_UNDERLYING},
        "etf_data_evidence": etf_evidence,
        "etf_raw_path": str(ETF_RAW),
        "etf_raw_sha256": sha256(ETF_RAW),
        "etf_dividend_path": str(ETF_DIVIDENDS),
        "etf_dividend_sha256": sha256(ETF_DIVIDENDS),
        "etf_yahoo_crosscheck_path": str(ETF_YAHOO_ADJUSTED),
        "etf_yahoo_crosscheck_sha256": sha256(ETF_YAHOO_ADJUSTED),
        "base_daily_path": str(BASE_DAILY),
        "base_daily_sha256": sha256(BASE_DAILY),
        "base_audit_path": str(BASE_AUDIT),
        "base_audit_sha256": sha256(BASE_AUDIT),
        "v80_source_sha256": sha256(V80_SOURCE),
        "v80_git_head": git_head(MOMENTUM_ROOT),
        "ic_im_git_head": git_head(ROOT),
        "v80_a_return_parity_max_abs": a_parity,
        "hldw100_overlap": overlap_stats,
        "annualization": {
            "cagr": "actual calendar span",
            "volatility_and_sharpe": 252.0,
            "sharpe_extra_risk_free_subtraction": 0.0,
        },
        "costs": {
            "underlying_strategy_costs": "preserved in frozen daily returns",
            "etf_fund_expenses_and_tracking": "embedded in actual market price",
            "initial_purchase_and_cross_sleeve_rebalance_cost": "not deducted",
        },
        "known_omissions": [
            "ETF bid-ask spread and market impact",
            "ETF premium-discount and creation-redemption frictions",
            "cross-strategy capital-transfer cost",
            "A-share price-limit executability",
            "temporary futures/options margin hikes",
        ],
    }

    output_daily = returns.copy()
    for column in output_daily.columns:
        output_daily[f"NAV_{column}"] = (1.0 + output_daily[column]).cumprod()
    output_daily.to_csv(OUTPUT / "daily_returns.csv", encoding="utf-8-sig", index_label="date")
    metrics.to_csv(OUTPUT / "window_metrics.csv", encoding="utf-8-sig", index=False)
    correlations.to_csv(OUTPUT / "correlations.csv", encoding="utf-8-sig")
    overlap.to_csv(OUTPUT / "a_hldw_overlap.csv", encoding="utf-8-sig", index_label="date")
    etf_path.loc[:formal_end].to_csv(OUTPUT / "etf_total_return.csv", encoding="utf-8-sig", index_label="date")
    (1.0 + returns[list(metric_series)]).resample("ME").prod().sub(1.0).to_csv(
        OUTPUT / "monthly_returns.csv", encoding="utf-8-sig", index_label="month"
    )
    (OUTPUT / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUTPUT / "report.md").write_text(build_report(metrics, audit), encoding="utf-8")
    (OUTPUT / "command_log.txt").write_text(f'python "{Path(__file__).name}"\n', encoding="utf-8")
    make_plot(returns, OUTPUT / "nav_drawdown.png")

    baseline = metric_lookup(metrics, "Baseline_A50_IC25_IM25")
    main = metric_lookup(metrics, "Main_A40_IC20_IM20_ETF20")
    print(f"Output: {OUTPUT}")
    print(f"Common formal sample: {component.index.min().date()} to {component.index.max().date()} ({len(component)} rows)")
    print(
        f"Baseline CAGR/MaxDD={baseline['cagr_calendar']:.6%}/{baseline['max_drawdown']:.6%}; "
        f"ETF20 main={main['cagr_calendar']:.6%}/{main['max_drawdown']:.6%}"
    )
    print(f"V8.0 A parity max abs: {a_parity:.3e}")


if __name__ == "__main__":
    main()
