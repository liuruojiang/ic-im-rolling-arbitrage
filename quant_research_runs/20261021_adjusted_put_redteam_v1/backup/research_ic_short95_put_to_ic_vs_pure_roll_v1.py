"""Compare the 95% short-Put-to-IC recovery study with pure 1x rolling IC."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_proxy_validation_v1 as proxy
import ic_roll_momentum_stage2_put_v2 as ic_put

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "ic_short95_put_to_ic_vs_pure_rolling_ic_v1"
REAL = ROOT / "quant_param_scan_runs" / "20260914_ic_short95_put_to_ic_recovery_v1" / "daily.csv.gz"
MODEL = ROOT / "quant_param_scan_runs" / (
    "20260914_ic_im_ic_short_95_put_etf_to_ic_recovery_model_extension_"
    "ic_short_put_physical_assignment_recovery_post_assignment_recovery_instrument"
) / "post_assignment_ic_daily.csv.gz"


def metrics(frame: pd.DataFrame, label: str, layer: str) -> list[dict]:
    rows = []
    for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
        start = frame.date.min() if years is None else frame.date.max() - pd.DateOffset(years=years)
        available = years is None or frame.date.min() <= start
        sub = frame[frame.date >= start] if available else frame.iloc[:0]
        result = proxy.metrics(sub.return_net) if available else {key: "N/A" for key in ("total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd")}
        rows.append({"layer": layer, "candidate": label, "segment": segment, "available": available, "reason": "" if available else "history shorter than requested window", "start": str(sub.date.min().date()) if available else "", "end": str(sub.date.max().date()) if available else "", "rows": len(sub), **result})
    return rows


def drawdown(frame: pd.DataFrame) -> dict:
    nav = (1.0 + frame.return_net).cumprod()
    dd = nav / nav.cummax() - 1.0
    trough = int(dd.idxmin())
    peak = int(nav.iloc[: trough + 1].idxmax())
    recovery = frame.iloc[trough + 1 :][nav.iloc[trough + 1 :].ge(nav.iloc[peak])]
    return {"max_dd": float(dd.iloc[trough]), "peak_date": str(frame.iloc[peak].date.date()), "trough_date": str(frame.iloc[trough].date.date()), "recovery_date": "" if recovery.empty else str(recovery.iloc[0].date.date())}


def main() -> None:
    if OUT.exists():
        raise FileExistsError(OUT)
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    pure = frames["ic"][["date", "ic_net_ret"]].copy()
    pure["return_net"] = pure.ic_net_ret.astype(float) + 0.70 * proxy.CASH_DAILY
    expected = pure.ic_net_ret + 0.70 * proxy.CASH_DAILY
    parity_error = float((pure.return_net - expected).abs().max())
    if parity_error > 1e-15:
        raise RuntimeError("Pure rolling IC return construction failed")
    sources = {"real": pd.read_csv(REAL, parse_dates=["date"]), "model": pd.read_csv(MODEL, parse_dates=["date"])}
    all_metrics, all_daily, all_dd = [], [], []
    for layer, short in sources.items():
        short = short[["date", "return_net", "nav", "state", "action"]].copy()
        short["candidate"] = "short95_put_then_ic"
        base = pure[pure.date.isin(short.date)][["date", "return_net"]].copy().reset_index(drop=True)
        short = short.reset_index(drop=True)
        if len(base) != len(short) or not np.array_equal(base.date.to_numpy(), short.date.to_numpy()):
            raise RuntimeError(f"{layer} common-calendar mismatch")
        base["candidate"], base["state"], base["action"] = "pure_1x_rolling_ic", "rolling_ic", ""
        base["nav"] = (1.0 + base.return_net).cumprod()
        for item in (short, base):
            all_metrics.extend(metrics(item, item.candidate.iloc[0], layer))
            all_dd.append({"layer": layer, "candidate": item.candidate.iloc[0], **drawdown(item)})
            item["layer"] = layer
            all_daily.append(item)
    table = pd.DataFrame(all_metrics)
    pivot = table.pivot(index=["layer", "segment"], columns="candidate", values=["ann_return", "ann_vol", "sharpe_repo", "max_dd", "total_return"]).reset_index()
    pivot.columns = ["_".join(str(x) for x in column if x) for column in pivot.columns]
    short_ann = pd.to_numeric(pivot["ann_return_short95_put_then_ic"], errors="coerce")
    pure_ann = pd.to_numeric(pivot["ann_return_pure_1x_rolling_ic"], errors="coerce")
    short_dd = pd.to_numeric(pivot["max_dd_short95_put_then_ic"], errors="coerce")
    pure_dd = pd.to_numeric(pivot["max_dd_pure_1x_rolling_ic"], errors="coerce")
    pivot["ann_return_delta_short_minus_pure"] = (short_ann - pure_ann).where(short_ann.notna() & pure_ann.notna(), "N/A")
    pivot["max_dd_improvement_short_minus_pure"] = (short_dd - pure_dd).where(short_dd.notna() & pure_dd.notna(), "N/A")
    OUT.mkdir(parents=True)
    pd.concat(all_daily, ignore_index=True).to_csv(OUT / "daily_comparison.csv.gz", index=False, compression="gzip")
    table.to_csv(OUT / "metrics_by_window.csv", index=False)
    pivot.to_csv(OUT / "comparison_by_window.csv", index=False)
    pd.DataFrame(all_dd).to_csv(OUT / "max_drawdown_episodes.csv", index=False)
    meta = {"status": "research_only_no_signal_or_order_change", "definition": {"short_put": "next-month 95% Put; physical assignment converts to active IC next open; exit IC next open after cycle PnL recovers exit cost", "pure_roll": "1x monthly rolling IC only; no Put, momentum or grid; ic_net_ret plus 70% cash at 3% annual"}, "data_layers": {"real": {"start": str(sources["real"].date.min().date()), "end": str(sources["real"].date.max().date()), "short_put": "actual 510500 option/ETF data", "pure_roll": "historical IC monthly roll"}, "model": {"start": str(sources["model"].date.min().date()), "end": str(sources["model"].date.max().date()), "short_put": "theoretical Black-Scholes 510500 Put and ETF-price proxy; IC historical quotes", "pure_roll": "historical IC monthly roll"}}, "parity": {"pure_roll_formula_max_abs_error": parity_error}, "limitations": "The model Put layer is not historical executable option evidence. Neither result models broker margin, bid-ask, impact, tax or capacity. Research only."}
    (OUT / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# 卖95% Put转IC vs 纯滚IC\n\n纯滚IC定义为1倍月度滚动IC，不含Put、动量和网格；收益为仓库的ic_net_ret加30%保证金缓冲剩余70%现金年化3%。卖Put路径沿用当前研究的实物交割后换IC、回本退出状态机。\n\n真实层为2022-09-19至2026-08-14真实510500期权/ETF；模型层为2015-04-16至2026-08-14理论Put和ETF代理、实际IC历史行情。模型层不与真实层拼接。\n\n## Metrics\n\n" + pivot.to_string(index=False) + "\n\n## Max drawdown\n\n" + pd.DataFrame(all_dd).to_string(index=False) + "\n\nDecision: research_only_no_promotion\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    (OUT / "command_log.txt").write_text("python -X utf8 research_ic_short95_put_to_ic_vs_pure_roll_v1.py\n", encoding="utf-8")
    print(pivot.to_string(index=False))
    print(pd.DataFrame(all_dd).to_string(index=False))


if __name__ == "__main__":
    main()
