from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_momentum_gated_roll_v1"
DAILY_PATH = OUTPUT / "daily_nav.csv"
UPSTREAM_PATH = (
    ROOT / "data" / "im_roll_momentum_blend_v1" / "upstream_im_roll_output" / "daily_nav.csv"
)
ANNUALIZATION_DAYS = 252.0
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


def cagr_and_dd(returns: pd.Series) -> tuple[float, float]:
    nav = (1.0 + returns.astype(float)).cumprod()
    return (
        float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / len(returns)) - 1.0),
        float((nav / nav.cummax() - 1.0).min()),
    )


def build_daily() -> pd.DataFrame:
    strategy = pd.read_csv(DAILY_PATH, parse_dates=["date"])
    upstream = pd.read_csv(UPSTREAM_PATH, parse_dates=["date"])[
        ["date", "csi1000_price_ret", "gross_vs_price_ret"]
    ]
    daily = strategy.merge(upstream, on="date", validate="one_to_one")
    daily["timed_price_gross_ret"] = daily["target_im_units"] * daily["csi1000_price_ret"]
    daily["basis_excess_exact_ret"] = (
        (1.0 + daily["futures_gross_ret"]) / (1.0 + daily["timed_price_gross_ret"]) - 1.0
    )
    daily["price_same_futures_cost_ret"] = (
        (1.0 + daily["timed_price_gross_ret"]) * (1.0 - daily["futures_cost_rate"])
        - 1.0
        + daily["cash_contribution_ret"]
    )
    daily["price_trade_cost_only_ret"] = (
        (1.0 + daily["timed_price_gross_ret"]) * (1.0 - daily["trade_cost_rate"])
        - 1.0
        + daily["cash_contribution_ret"]
    )
    exact = (
        (1.0 + daily["timed_price_gross_ret"])
        * (1.0 + daily["basis_excess_exact_ret"])
        - 1.0
    )
    if (exact - daily["futures_gross_ret"]).abs().max() > 1e-12:
        raise RuntimeError("Direction and basis factors do not reconstruct IM gross return")
    flat = daily["target_im_units"].eq(0.0)
    if daily.loc[flat, "basis_excess_exact_ret"].abs().max() > 1e-15:
        raise RuntimeError("Flat dates contain basis exposure")
    return daily


def metric_row(sample: pd.DataFrame) -> dict[str, float]:
    im_ann, im_dd = cagr_and_dd(sample["strategy_ret"])
    same_cost_ann, same_cost_dd = cagr_and_dd(sample["price_same_futures_cost_ret"])
    price_ann, price_dd = cagr_and_dd(sample["price_trade_cost_only_ret"])
    basis_cumulative = float((1.0 + sample["basis_excess_exact_ret"]).prod() - 1.0)
    basis_ann = float((1.0 + basis_cumulative) ** (ANNUALIZATION_DAYS / len(sample)) - 1.0)
    return {
        "rows": float(len(sample)),
        "im_strategy_ann_return": im_ann,
        "im_strategy_max_dd": im_dd,
        "timed_price_proxy_ann_return": price_ann,
        "timed_price_proxy_max_dd": price_dd,
        "timed_price_same_futures_cost_ann_return": same_cost_ann,
        "timed_price_same_futures_cost_max_dd": same_cost_dd,
        "gross_basis_cumulative": basis_cumulative,
        "gross_basis_ann_return": basis_ann,
        "im_vs_price_proxy_ann_delta_pp": 100.0 * (im_ann - price_ann),
        "roll_cost_ann_drag_pp": 100.0 * (same_cost_ann - price_ann),
        "trade_cost_sum": float(sample["trade_cost_rate"].sum()),
        "roll_cost_sum": float(sample["roll_cost_rate"].sum()),
        "avg_im_units": float(sample["target_im_units"].mean()),
    }


def build_windows(daily: pd.DataFrame) -> pd.DataFrame:
    end = daily["date"].max()
    rows: list[dict[str, object]] = []
    for window, offset in WINDOWS:
        requested_start = None if offset is None else end - offset
        sample = daily if requested_start is None else daily.loc[daily["date"] >= requested_start]
        actual_start = sample["date"].min()
        available = bool(
            requested_start is None or actual_start <= requested_start + pd.Timedelta(days=7)
        )
        values = metric_row(sample) if available else {
            key: np.nan
            for key in metric_row(daily.iloc[:2]).keys()
        }
        rows.append(
            {
                "window": window,
                "available": available,
                "unavailable_reason": "" if available else (
                    f"IM history starts {daily['date'].min().date()}, shorter than requested {window} window"
                ),
                "start": actual_start.date().isoformat(),
                "end": sample["date"].max().date().isoformat(),
                **values,
            }
        )
    return pd.DataFrame(rows)


def build_annual(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, sample in daily.groupby(daily["date"].dt.year):
        rows.append(
            {
                "year": int(year),
                "start": sample["date"].min().date().isoformat(),
                "end": sample["date"].max().date().isoformat(),
                **metric_row(sample),
            }
        )
    return pd.DataFrame(rows)


def pct(value: object) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{100.0 * float(value):.2f}%"


def write_record(windows: pd.DataFrame, manifest: dict[str, object]) -> None:
    rows = [
        "|窗口|IM动量门控|价格指数方向代理|IM相对指数毛基差年化|毛基差累计|IM相对价格代理年化差|换月成本年化拖累|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in windows.itertuples(index=False):
        if not row.available:
            rows.append(f"|{row.window}|N/A|N/A|N/A|N/A|N/A|N/A|")
            continue
        rows.append(
            f"|{row.window}|{pct(row.im_strategy_ann_return)} / {pct(row.im_strategy_max_dd)}|"
            f"{pct(row.timed_price_proxy_ann_return)} / {pct(row.timed_price_proxy_max_dd)}|"
            f"{pct(row.gross_basis_ann_return)}|{pct(row.gross_basis_cumulative)}|"
            f"{row.im_vs_price_proxy_ann_delta_pp:+.2f}pp|{row.roll_cost_ann_drag_pp:+.2f}pp|"
        )
    text = f"""# IM 动量门控持仓期贴水/基差拆分

状态：正式研究诊断；未批准实盘  
数据截止：{manifest['sample']['end']}

## 定义

- `IM动量门控`：实际策略，持仓收益使用逐月IM官方结算价，已经包含指数方向和持有期间的基差收敛。
- `价格指数方向代理`：保持完全相同的0/0.5/1动量仓位、现金收益和进出成本，但把IM收益替换为中证1000价格指数收益，并移除IM换月成本。
- `IM相对指数毛基差`：逐日精确因子 `(1 + weight * IM收益) / (1 + weight * 指数收益) - 1`；它包含贴水收敛、基差变动及期货相对价格指数的跟踪差，不等同于某一天看到的静态贴水率。

## 结果

{chr(10).join(rows)}

## 机制说明

换月当天按旧合约最终结算价结束，并按同日新合约结算价建立下一月仓位；买入新合约的贴水不会在换月当天凭空记成利润，而是在随后持有并向现货收敛的过程中进入IM逐日收益。因此，贴水位于 `im_gross_ret`，不是额外再加一次的固定收益。

全样本共{manifest['integrity']['rows']}日，其中持仓换月{manifest['integrity']['held_roll_events']}次；换月成本累计{manifest['integrity']['roll_cost_sum']:.4%}，进出成本累计{manifest['integrity']['trade_cost_sum']:.4%}。

## 边界

这是基于价格指数的基差分解。IM相对价格指数的超额还会受到股息预期、融资利率、对冲需求、流动性和期货跟踪误差影响，不能把全部超额机械称为无风险贴水收益。
"""
    (OUTPUT / "basis_decomposition_record.md").write_text(text, encoding="utf-8")


def run() -> None:
    targets = [
        OUTPUT / "basis_decomposition_daily.csv.gz",
        OUTPUT / "basis_decomposition_by_window.csv",
        OUTPUT / "basis_decomposition_annual.csv",
        OUTPUT / "basis_decomposition_manifest.json",
        OUTPUT / "basis_decomposition_record.md",
        OUTPUT / "basis_decomposition_command_log.txt",
    ]
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError(f"Decomposition artifacts already exist: {existing}")
    daily = build_daily()
    windows = build_windows(daily)
    annual = build_annual(daily)
    daily.to_csv(targets[0], index=False, compression="gzip", encoding="utf-8-sig")
    windows.to_csv(targets[1], index=False, encoding="utf-8-sig")
    annual.to_csv(targets[2], index=False, encoding="utf-8-sig")
    command = f"{Path(sys.executable).name} {Path(__file__).name}"
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "inputs": {
            "strategy_daily": str(DAILY_PATH),
            "strategy_daily_sha256": sha256_file(DAILY_PATH),
            "upstream_daily": str(UPSTREAM_PATH),
            "upstream_daily_sha256": sha256_file(UPSTREAM_PATH),
        },
        "sample": {
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "rows": int(len(daily)),
        },
        "integrity": {
            "rows": int(len(daily)),
            "held_roll_events": int((daily["roll_event"] & daily["target_im_units"].gt(0)).sum()),
            "roll_cost_sum": float(daily["roll_cost_rate"].sum()),
            "trade_cost_sum": float(daily["trade_cost_rate"].sum()),
            "direction_basis_reconstruction_max_abs": float(
                (
                    (1.0 + daily["timed_price_gross_ret"])
                    * (1.0 + daily["basis_excess_exact_ret"])
                    - 1.0
                    - daily["futures_gross_ret"]
                ).abs().max()
            ),
        },
    }
    targets[3].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    targets[5].write_text(command + "\n", encoding="utf-8")
    write_record(windows, manifest)


if __name__ == "__main__":
    run()
