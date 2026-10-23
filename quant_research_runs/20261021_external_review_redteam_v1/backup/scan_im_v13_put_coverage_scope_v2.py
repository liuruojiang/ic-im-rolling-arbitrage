from __future__ import annotations

from pathlib import Path

import pandas as pd

import scan_im_v13_put_coverage_scope_v1 as v1


ROOT = Path(__file__).resolve().parent
ORIGINAL_BUILD_DAILY = v1.build_daily
ORIGINAL_WRITE_RECORD = v1.write_record
VERSION = "im_v13_put_coverage_scope_ablation_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_execution_timing_put_coverage_scope_timing"
)


def lag_within_data_layer(series: pd.Series, dates: pd.Series) -> pd.Series:
    lagged = series.shift(1, fill_value=0.0).astype(float)
    lagged.loc[dates.eq(v1.REAL_START)] = 0.0
    return lagged


def build_daily(frame: pd.DataFrame):
    daily, parity = ORIGINAL_BUILD_DAILY(frame)
    daily["put_pnl_scale"] = daily["coverage_scale"].astype(float)
    parent_put_pnl = frame["put_pnl_ret"].astype(float).reset_index(drop=True)
    dates = frame["date"].reset_index(drop=True)
    scales = v1.coverage_scales(frame)
    for candidate in v1.CANDIDATES:
        mask = daily["candidate"].eq(candidate)
        block = daily.loc[mask].copy().reset_index(drop=True)
        current_scale = scales[candidate].reset_index(drop=True)
        # The core Put already exists in v5 and retains its exact timing. Only
        # newly covered momentum/grid units begin earning Put P&L after their
        # close execution.
        if candidate == "no_put":
            pnl_scale = pd.Series(0.0, index=block.index)
        elif candidate == "core_only_current":
            pnl_scale = pd.Series(0.5, index=block.index)
        else:
            extra = (current_scale - 0.5).clip(lower=0.0)
            pnl_scale = 0.5 + lag_within_data_layer(extra, dates)
        put_pnl = parent_put_pnl * pnl_scale
        pre_cash = (
            (
                1.0
                + block["futures_gross_ret"].astype(float)
                + put_pnl
                + block["call_pnl_ret"].astype(float)
            )
            * (1.0 - block["futures_cost_rate"].astype(float))
            * (1.0 - block["put_cost_rate"].astype(float))
            * (1.0 - block["call_cost_rate"].astype(float))
            - 1.0
        )
        ret = pre_cash + block["cash_weight"].astype(float) * v1.fixed.im_proxy.CASH_DAILY_RETURN
        block["put_pnl_scale"] = pnl_scale
        block["put_pnl_ret"] = put_pnl
        block["ret"] = ret
        block["nav"] = (1.0 + block["ret"]).cumprod()
        block["drawdown"] = block["nav"] / block["nav"].cummax() - 1.0
        daily.loc[mask, block.columns] = block.to_numpy()

    current = daily[daily["candidate"].eq("core_only_current")].reset_index(drop=True)
    frozen = pd.read_csv(v1.FIXED_DAILY, parse_dates=["date"])
    parity["v2_current_ret_vs_v5_max_abs"] = float((current["ret"] - frozen["ret"]).abs().max())
    if parity["v2_current_ret_vs_v5_max_abs"] > 1e-12:
        raise RuntimeError(f"v2 core-only baseline parity failed: {parity}")
    return daily, parity


def write_record(*args, **kwargs):
    ORIGINAL_WRITE_RECORD(*args, **kwargs)
    path = RUN / "record.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("# IM v1.3 Put 覆盖范围消融 v1", "# IM v1.3 Put 覆盖范围消融 v2（收盘时序）", 1)
    marker = "## Cost and Execution Assumptions\n\n"
    timing = (
        "- v2保守时序：动量/网格新增Put在当日收盘成交，当日损益按上一交易日已持有的新增覆盖单位计算；"
        "日末数量、市值与成本按当日目标。\n"
    )
    text = text.replace(marker, marker + timing, 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    v1.VERSION = VERSION
    v1.SPEC = SPEC
    v1.SPEC_HASH = SPEC_HASH
    v1.RUN = RUN
    v1.__file__ = str(Path(__file__).resolve())
    v1.build_daily = build_daily
    v1.write_record = write_record
    v1.main()


if __name__ == "__main__":
    main()
