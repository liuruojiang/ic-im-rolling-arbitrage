from __future__ import annotations

import calendar
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_proxy_v1"
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SIGNAL_PATH = ROOT / "data" / "im_roll_momentum_blend_v1" / "official_000852_signal.csv"
REAL_UPSTREAM_PATH = (
    ROOT / "data" / "im_roll_momentum_blend_v1" / "upstream_im_roll_output" / "daily_nav.csv"
)
REAL_MOMENTUM_PATH = ROOT / "outputs" / "im_momentum_gated_roll_v1" / "daily_nav.csv"
REAL_BLEND_PATH = ROOT / "outputs" / "im_roll50_momentum50_v1" / "daily_nav.csv"
OUTPUT_DIR = ROOT / "outputs" / VERSION
REAL_START = pd.Timestamp("2022-07-22")
ONE_WAY_COST = 0.0001
MARGIN_BUFFER_RATE = 0.30
CASH_ANNUAL_RETURN = 0.03
ANNUALIZATION_DAYS = 252.0
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / ANNUALIZATION_DAYS) - 1.0
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> str:
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Specification hash mismatch: {expected} != {actual}")
    return actual


def third_friday(year: int, month: int) -> pd.Timestamp:
    weeks = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in weeks if week[calendar.FRIDAY] != 0]
    return pd.Timestamp(year=year, month=month, day=fridays[2])


def proxy_roll_dates(dates: pd.DatetimeIndex) -> set[pd.Timestamp]:
    result: set[pd.Timestamp] = set()
    periods = pd.period_range(dates.min().to_period("M"), dates.max().to_period("M"), freq="M")
    for period in periods:
        rule_date = third_friday(period.year, period.month)
        candidates = dates[(dates.year == period.year) & (dates.month == period.month) & (dates <= rule_date)]
        if len(candidates):
            chosen = pd.Timestamp(candidates.max())
            if chosen > dates.min():
                result.add(chosen)
    return result


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for path in (SIGNAL_PATH, REAL_UPSTREAM_PATH, REAL_MOMENTUM_PATH, REAL_BLEND_PATH):
        if not path.exists():
            raise FileNotFoundError(path)
    signal = pd.read_csv(SIGNAL_PATH, parse_dates=["date"])[
        [
            "date", "csi1000_signal_close", "score", "abs20", "desired_weight",
            "momentum_weight",
        ]
    ].sort_values("date")
    real = pd.read_csv(REAL_UPSTREAM_PATH, parse_dates=["date"])[
        [
            "date", "contract", "im_gross_ret", "cost_rate", "csi1000_price_ret",
            "roll_from", "roll_to", "im_net_plus_cash_ret",
        ]
    ].sort_values("date")
    real_momentum = pd.read_csv(REAL_MOMENTUM_PATH, parse_dates=["date"])[
        ["date", "strategy_ret", "target_im_units"]
    ].rename(columns={"strategy_ret": "formal_momentum_ret"})
    real_blend = pd.read_csv(REAL_BLEND_PATH, parse_dates=["date"])[
        ["date", "blend_ret"]
    ].rename(columns={"blend_ret": "formal_blend_ret"})
    if real["date"].min() != REAL_START or signal["date"].max() != real["date"].max():
        raise RuntimeError("Unexpected signal or real IM date range")
    return signal, real, real_momentum, real_blend


def post_listing_basis(real: pd.DataFrame) -> dict[str, float]:
    factor = (1.0 + real["im_gross_ret"]) / (1.0 + real["csi1000_price_ret"])
    if factor.le(0).any():
        raise RuntimeError("Invalid post-listing basis factor")
    daily = float(factor.prod() ** (1.0 / len(factor)) - 1.0)
    annual = float((1.0 + daily) ** ANNUALIZATION_DAYS - 1.0)
    cumulative = float(factor.prod() - 1.0)
    return {
        "rows": int(len(real)),
        "daily_geometric": daily,
        "annual_geometric": annual,
        "cumulative": cumulative,
    }


def build_extended_daily(
    signal: pd.DataFrame,
    real: pd.DataFrame,
    real_momentum: pd.DataFrame,
    real_blend: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    basis = post_listing_basis(real)
    daily = signal.loc[signal["date"] <= real["date"].max()].copy()
    daily["csi1000_price_ret"] = daily["csi1000_signal_close"].pct_change().fillna(0.0)
    daily = daily.merge(
        real[
            [
                "date", "contract", "im_gross_ret", "cost_rate", "roll_from", "roll_to",
                "im_net_plus_cash_ret",
            ]
        ].rename(
            columns={
                "im_gross_ret": "real_im_gross_ret",
                "cost_rate": "real_bare_cost_rate",
                "roll_from": "real_roll_from",
                "roll_to": "real_roll_to",
                "im_net_plus_cash_ret": "formal_bare_ret",
            }
        ),
        on="date",
        how="left",
        validate="one_to_one",
    )
    daily = daily.merge(real_momentum, on="date", how="left", validate="one_to_one")
    daily = daily.merge(real_blend, on="date", how="left", validate="one_to_one")
    daily["phase"] = np.where(daily["date"] < REAL_START, "prelisting_proxy", "real_im")
    proxy = daily["phase"].eq("prelisting_proxy")
    real_mask = ~proxy
    daily["im_gross_ret_extended"] = daily["real_im_gross_ret"]
    daily.loc[proxy, "im_gross_ret_extended"] = (
        (1.0 + daily.loc[proxy, "csi1000_price_ret"])
        * (1.0 + basis["daily_geometric"])
        - 1.0
    )

    proxy_dates = pd.DatetimeIndex(daily.loc[proxy, "date"])
    proxy_rolls = proxy_roll_dates(proxy_dates)
    daily["roll_event"] = False
    daily.loc[proxy, "roll_event"] = daily.loc[proxy, "date"].isin(proxy_rolls)
    daily.loc[real_mask, "roll_event"] = (
        daily.loc[real_mask, "real_roll_to"].fillna("").astype(str).ne("")
    )

    daily["bare_cost_rate"] = daily["real_bare_cost_rate"]
    daily.loc[proxy, "bare_cost_rate"] = (
        2.0 * ONE_WAY_COST * daily.loc[proxy, "roll_event"].astype(float)
    )
    daily.loc[daily.index[0], "bare_cost_rate"] += ONE_WAY_COST

    daily["momentum_turnover"] = daily["momentum_weight"].diff().abs()
    daily.loc[daily.index[0], "momentum_turnover"] = abs(float(daily.loc[daily.index[0], "momentum_weight"]))
    real_first_index = daily.index[daily["date"].eq(REAL_START)]
    if len(real_first_index) != 1:
        raise RuntimeError("Missing or duplicate real IM start date")
    real_first = int(real_first_index[0])
    daily.loc[real_first, "momentum_turnover"] = abs(float(daily.loc[real_first, "momentum_weight"]))
    daily["momentum_trade_cost_rate"] = ONE_WAY_COST * daily["momentum_turnover"]
    daily["momentum_roll_cost_rate"] = (
        2.0 * ONE_WAY_COST * daily["momentum_weight"] * daily["roll_event"].astype(float)
    )
    daily["momentum_cost_rate"] = (
        daily["momentum_trade_cost_rate"] + daily["momentum_roll_cost_rate"]
    )

    daily["bare_futures_ret"] = (
        (1.0 + daily["im_gross_ret_extended"]) * (1.0 - daily["bare_cost_rate"]) - 1.0
    )
    daily["bare_cash_weight"] = 1.0 - MARGIN_BUFFER_RATE
    daily["bare_roll_ret"] = daily["bare_futures_ret"] + daily["bare_cash_weight"] * CASH_DAILY_RETURN

    daily["momentum_futures_gross_ret"] = (
        daily["momentum_weight"] * daily["im_gross_ret_extended"]
    )
    daily["momentum_futures_ret"] = (
        (1.0 + daily["momentum_futures_gross_ret"])
        * (1.0 - daily["momentum_cost_rate"])
        - 1.0
    )
    daily["momentum_cash_weight"] = 1.0 - MARGIN_BUFFER_RATE * daily["momentum_weight"]
    daily["momentum_gated_ret"] = (
        daily["momentum_futures_ret"] + daily["momentum_cash_weight"] * CASH_DAILY_RETURN
    )

    daily["blend_ret"] = 0.5 * daily["bare_roll_ret"] + 0.5 * daily["momentum_gated_ret"]
    daily["total_im_units"] = 0.5 + 0.5 * daily["momentum_weight"]
    daily["blend_cash_weight"] = 0.5 * daily["bare_cash_weight"] + 0.5 * daily["momentum_cash_weight"]
    daily["blend_nav"] = (1.0 + daily["blend_ret"]).cumprod()

    if daily.loc[real_mask, ["real_im_gross_ret", "formal_bare_ret", "formal_momentum_ret", "formal_blend_ret"]].isna().any().any():
        raise RuntimeError("Missing real-period parity inputs")
    parity = {
        "im_gross": float(
            (daily.loc[real_mask, "im_gross_ret_extended"] - daily.loc[real_mask, "real_im_gross_ret"]).abs().max()
        ),
        "bare_ret": float(
            (daily.loc[real_mask, "bare_roll_ret"] - daily.loc[real_mask, "formal_bare_ret"]).abs().max()
        ),
        "momentum_ret": float(
            (daily.loc[real_mask, "momentum_gated_ret"] - daily.loc[real_mask, "formal_momentum_ret"]).abs().max()
        ),
        "blend_ret": float(
            (daily.loc[real_mask, "blend_ret"] - daily.loc[real_mask, "formal_blend_ret"]).abs().max()
        ),
    }
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"Real-period endpoint parity failed: {parity}")
    if not set(daily["total_im_units"].unique()).issubset({0.5, 0.75, 1.0}):
        raise RuntimeError("Unexpected full-cycle combined exposure")
    if (daily["blend_cash_weight"] - (1.0 - MARGIN_BUFFER_RATE * daily["total_im_units"])).abs().max() > 1e-12:
        raise RuntimeError("Full-cycle cash accounting mismatch")
    return daily, {
        "basis": basis,
        "proxy_roll_count": len(proxy_rolls),
        "real_roll_count": int(daily.loc[real_mask, "roll_event"].sum()),
        "real_parity_max_abs": parity,
    }


def metric_values(returns: pd.Series) -> dict[str, float]:
    nav = (1.0 + returns.astype(float)).cumprod()
    ann_return = float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / len(returns)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    return {
        "rows": float(len(returns)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": float((nav / nav.cummax() - 1.0).min()),
        "sharpe_repo": float(ann_return / ann_vol) if ann_vol > 1e-12 else 0.0,
        "final_nav": float(nav.iloc[-1]),
    }


def build_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    columns = {
        "bare_roll_im_extended": "bare_roll_ret",
        "momentum_gated_roll_im_extended": "momentum_gated_ret",
        "roll50_momentum50_extended": "blend_ret",
    }
    end = daily["date"].max()
    rows: list[dict[str, object]] = []
    for strategy, column in columns.items():
        for window, offset in WINDOWS:
            requested_start = None if offset is None else end - offset
            sample = daily if requested_start is None else daily.loc[daily["date"] >= requested_start]
            actual_start = sample["date"].min()
            available = bool(
                requested_start is None or actual_start <= requested_start + pd.Timedelta(days=7)
            )
            proxy_rows = int(sample["phase"].eq("prelisting_proxy").sum())
            real_rows = int(sample["phase"].eq("real_im").sum())
            values = metric_values(sample[column]) if available else {
                key: np.nan
                for key in ("rows", "ann_return", "ann_vol", "max_dd", "sharpe_repo", "final_nav")
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "start": actual_start.date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "proxy_rows": proxy_rows,
                    "real_rows": real_rows,
                    "proxy_share": proxy_rows / len(sample),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def build_annual(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    columns = {
        "bare_roll_im_extended": "bare_roll_ret",
        "momentum_gated_roll_im_extended": "momentum_gated_ret",
        "roll50_momentum50_extended": "blend_ret",
    }
    for year, sample in daily.groupby(daily["date"].dt.year):
        for strategy, column in columns.items():
            rows.append(
                {
                    "year": int(year),
                    "strategy": strategy,
                    "phase": "proxy" if sample["phase"].eq("prelisting_proxy").all() else (
                        "real" if sample["phase"].eq("real_im").all() else "mixed"
                    ),
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def pct(value: object) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{100.0 * float(value):.2f}%"


def write_record(daily: pd.DataFrame, metrics: pd.DataFrame, audit: dict[str, object], manifest: dict[str, object]) -> None:
    labels = {
        "bare_roll_im_extended": "单纯滚IM（延伸）",
        "momentum_gated_roll_im_extended": "动量门控滚IM（延伸）",
        "roll50_momentum50_extended": "50%滚IM + 50%动量门控（延伸）",
    }
    rows = ["|策略|全样本|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in labels.items():
        block = metrics.loc[metrics["strategy"].eq(strategy)].set_index("window")
        values = [
            f"{pct(block.loc[window, 'ann_return'])} / {pct(block.loc[window, 'max_dd'])}"
            for window, _ in WINDOWS
        ]
        rows.append(f"|{label}|{'|'.join(values)}|")
    blend = metrics.loc[metrics["strategy"].eq("roll50_momentum50_extended")]
    coverage_rows = ["|窗口|起点|代理日|真实IM日|代理占比|", "|---|---:|---:|---:|---:|"]
    for row in blend.itertuples(index=False):
        coverage_rows.append(
            f"|{row.window}|{row.start}|{row.proxy_rows}|{row.real_rows}|{row.proxy_share:.2%}|"
        )
    text = f"""# 50%滚IM + 50%动量门控：中证1000全周期代理 v1

状态：代理情景研究完成；未批准实盘  
数据截止：{manifest['sample']['end']}

## 1. Scope

- 将固定50/50结构延伸到中证1000价格指数可测起点2014-10-17。
- 2014-10-17至2022-07-21为上市前代理；2022-07-22之后为真实IM。
- 结果是用户指定假设下的真实指数+代理期货情景，不是全周期可执行IM回测。

## 2. Code and Data Provenance

- 方向与信号：中证指数官方`000852`价格指数；真实IM：中金所官方逐月结算价。
- 上市后991日IM相对价格指数的几何年化毛基差为{audit['basis']['annual_geometric']:.2%}；上市前按对应固定日度基差回填。
- 时区Asia/Shanghai，价格指数不复权。

## 3. Execution Details

- 动量参数`MA35 / Mom18 / W2.5`，50% Abs20 OFF + 50%静态`Abs20 > 0`，T到T+1。
- 上市前按每月第三个星期五代理换月；真实期使用正式IM换月日。
- 单边1bp，每次换月两边2bp；30%/倍保证金及缓冲，其余现金按净年化3%。
- Put、Call与网格全部关闭。

## 4. Comparison Setup

- 三条路径使用相同扩展IM收益、同一信号、日历、成本和现金假设。
- 50/50组合逐日等权合成两袖净收益，总IM名义0.5/0.75/1倍。

## 5. Key Results

每格为年化收益 / 最大回撤；全样本、10年和5年包含代理期。

{chr(10).join(rows)}

### 窗口的数据构成

{chr(10).join(coverage_rows)}

## 6. Trading Frictions and Constraints

- 上市前代理期包含初始成本和每月换仓成本；真实期成本与正式上游完全一致。
- 未含盘口滑点、冲击成本、动态保证金、容量和极端情况下最终结算不可成交风险。

## 7. Integrity Checks

- 真实IM期毛收益、单纯滚IM、动量门控和50/50组合逐日复现最大误差均不超过{max(audit['real_parity_max_abs'].values()):.3e}。
- 上市前代理换月{audit['proxy_roll_count']}次，真实IM换月{audit['real_roll_count']}次。
- 组合总IM名义及现金权重检查通过；无Put、Call或网格字段。

## 8. Risks and Caveats

- 上市前{audit['basis']['annual_geometric']:.2%}毛基差来自2022年以后数据，构成明确的未来信息回填；不能据此声称策略在2014年可真实执行。
- 固定平均基差抹平了历史基差波动、拥挤和制度变化，尤其可能高估早期贴水稳定性。
- 本版没有做贴水均值上下浮动的敏感性扫描。

## 9. Backup and Rollback

- 本版只新增独立规格、脚本和输出，不覆盖真实IM研究；回滚方式为停用本代理路径。

## Decision

- `fullcycle_proxy_completed_not_executable_evidence`：保留为全周期情景证据，不晋升实盘。
"""
    (OUTPUT_DIR / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    if OUTPUT_DIR.exists():
        raise FileExistsError(f"Formal output already exists: {OUTPUT_DIR}")
    spec_hash = verify_spec()
    signal, real, real_momentum, real_blend = load_inputs()
    daily, audit = build_extended_daily(signal, real, real_momentum, real_blend)
    metrics = build_metrics(daily)
    annual = build_annual(daily)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT_DIR / "daily_nav.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    metrics.to_csv(OUTPUT_DIR / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT_DIR / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    command = f"{Path(sys.executable).name} {Path(__file__).name}"
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "proxy_scenario_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "spec_sha256": spec_hash,
        "sample": {
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "rows": int(len(daily)),
            "proxy_rows": int(daily["phase"].eq("prelisting_proxy").sum()),
            "real_rows": int(daily["phase"].eq("real_im").sum()),
            "timezone": "Asia/Shanghai",
        },
        "inputs": {
            "signal": str(SIGNAL_PATH),
            "signal_sha256": sha256_file(SIGNAL_PATH),
            "real_upstream": str(REAL_UPSTREAM_PATH),
            "real_upstream_sha256": sha256_file(REAL_UPSTREAM_PATH),
            "real_momentum": str(REAL_MOMENTUM_PATH),
            "real_momentum_sha256": sha256_file(REAL_MOMENTUM_PATH),
            "real_blend": str(REAL_BLEND_PATH),
            "real_blend_sha256": sha256_file(REAL_BLEND_PATH),
        },
        "proxy_assumption": {
            "basis_definition": "geometric mean of (1+IM gross return)/(1+CSI1000 price return)-1",
            **audit["basis"],
            "lookahead_warning": "Post-listing average basis is backfilled into the pre-listing period.",
        },
        "execution": {
            "one_way_cost": ONE_WAY_COST,
            "proxy_roll_rule": "third Friday or prior index trading day",
            "proxy_roll_count": audit["proxy_roll_count"],
            "real_roll_count": audit["real_roll_count"],
            "margin_buffer_per_1x": MARGIN_BUFFER_RATE,
            "cash_assumed_net_annual_return": CASH_ANNUAL_RETURN,
            "put": "excluded",
            "call": "excluded",
            "grid": "excluded",
        },
        "real_parity_max_abs": audit["real_parity_max_abs"],
        "decision": "fullcycle_proxy_completed_not_executable_evidence",
    }
    (OUTPUT_DIR / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(daily, metrics, audit, manifest)


if __name__ == "__main__":
    run()
