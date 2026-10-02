"""Matched CSI500 momentum-sleeve study; no IC/IM production edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_4_bot as icim


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "sina_sh000905_ohlcv.csv"
FORMAL_START = pd.Timestamp("2007-01-15")
CASH_DAILY = 1.02 ** (1.0 / 244.0) - 1.0
COST_RATE = 0.001


def build_arm(ohlcv: pd.DataFrame, *, ma: int, weight_end: float, abs_days: int, mode: str) -> pd.DataFrame:
    indicator_close = ohlcv["close"].astype(float)
    close = indicator_close.loc[indicator_close.index >= FORMAL_START]
    old_rule = icim.MOMENTUM_RULES["IC"].copy()
    try:
        icim.MOMENTUM_RULES["IC"].update(ma=ma, days=24, weight_end=weight_end)
        score = icim.calc_v13_momentum_score("IC", indicator_close).reindex(close.index)
    finally:
        icim.MOMENTUM_RULES["IC"].clear()
        icim.MOMENTUM_RULES["IC"].update(old_rule)
    abs_mom = (indicator_close / indicator_close.shift(abs_days) - 1.0).reindex(close.index)
    static_on = abs_mom.gt(0.0)
    debounce_on = icim._recovery_confirmed(abs_mom)
    if mode == "static":
        abs_on = static_on
    elif mode == "debounce_all":
        abs_on = debounce_on
    elif mode == "icim_effective":
        abs_on = static_on.where(
            close.index < pd.Timestamp(icim.MOMENTUM_DEBOUNCE_EFFECTIVE_DATE), debounce_on
        )
    else:
        raise ValueError(mode)
    base_target = (score > 0).astype(float) * (0.5 + 0.5 * abs_on.astype(float))
    base_execution = base_target.shift(1, fill_value=0.0)
    raw_ret = close.pct_change().fillna(0.0)
    base_turnover = base_execution.diff().abs().fillna(base_execution.abs())
    base_cash_ret = (1.0 - base_execution) * CASH_DAILY
    base_cash_ret.iloc[0] = 0.0
    base_ret = base_execution * raw_ret + base_cash_ret - COST_RATE * base_turnover
    base_nav = (1.0 + base_ret).cumprod()
    base_dd = base_nav / base_nav.cummax() - 1.0
    nav_gate = base_dd.le(-0.06)
    signal_target = base_target.where(~nav_gate, base_target * 0.5)
    execution_weight = signal_target.shift(1, fill_value=0.0)
    turnover = execution_weight.diff().abs().fillna(execution_weight.abs())
    cash_ret = (1.0 - execution_weight) * CASH_DAILY
    cash_ret.iloc[0] = 0.0
    strategy_ret = execution_weight * raw_ret + cash_ret - COST_RATE * turnover
    no_cost_ret = execution_weight * raw_ret + cash_ret
    return pd.DataFrame(
        {
            "close": close,
            "score": score,
            "abs_mom": abs_mom,
            "static_abs_on": static_on,
            "abs_on": abs_on,
            "base_target": base_target,
            "base_nav": base_nav,
            "base_dd": base_dd,
            "nav_gate": nav_gate,
            "signal_target": signal_target,
            "execution_weight": execution_weight,
            "raw_ret": raw_ret,
            "turnover": turnover,
            "cash_ret": cash_ret,
            "strategy_ret": strategy_ret,
            "nav": (1.0 + strategy_ret).cumprod(),
            "no_cost_nav_same_path": (1.0 + no_cost_ret).cumprod(),
        }
    )


def metrics(frame: pd.DataFrame, start: pd.Timestamp | None) -> dict[str, float | int | str]:
    sample = frame if start is None else frame.loc[frame.index >= start]
    ret = sample["strategy_ret"]
    nav = np.r_[1.0, (1.0 + ret.to_numpy()).cumprod()]
    dd = nav / np.maximum.accumulate(nav) - 1.0
    weight = sample["execution_weight"]
    changed = weight.diff().fillna(0.0).ne(0.0)
    return {
        "start": str(sample.index[0].date()),
        "end": str(sample.index[-1].date()),
        "rows": len(sample),
        "total_return": float(nav[-1] - 1.0),
        "annual_return_244": float(nav[-1] ** (244.0 / len(sample)) - 1.0),
        "max_dd": float(dd.min()),
        "mean_execution_weight": float(weight.mean()),
        "turnover": float(sample["turnover"].sum()),
        "execution_switches": int(changed.sum()),
        "half_to_full": int(((weight.shift(1) == 0.5) & (weight == 1.0)).sum()),
        "full_to_half": int(((weight.shift(1) == 1.0) & (weight == 0.5)).sum()),
        "nav_gate_days": int(sample["nav_gate"].sum()),
    }


def main() -> None:
    data = pd.read_csv(INPUT, parse_dates=["date"]).set_index("date")
    assert len(data) == 5282 and data.index[-1] == pd.Timestamp("2026-09-30")
    assert data.index.is_monotonic_increasing and not data.index.has_duplicates
    with icim.runtime_clock(pd.Timestamp("2026-10-02 14:00", tz="Asia/Shanghai").to_pydatetime()):
        icim._validate_v13_ohlcv("IC", data, icim._now_beijing())

    arms = {
        "old_icim_effective": build_arm(data, ma=110, weight_end=2.0, abs_days=20, mode="icim_effective"),
        "old_static": build_arm(data, ma=110, weight_end=2.0, abs_days=20, mode="static"),
        "new_static": build_arm(data, ma=105, weight_end=1.6, abs_days=40, mode="static"),
        "new_debounce_all": build_arm(data, ma=105, weight_end=1.6, abs_days=40, mode="debounce_all"),
        "new_debounce_effective": build_arm(data, ma=105, weight_end=1.6, abs_days=40, mode="icim_effective"),
    }

    # Reconcile the unchanged baseline with the formal IC/IM call chain.
    official = icim.v13_momentum_schedule("IC", data["close"], ohlcv=data)
    old = arms["old_icim_effective"]
    checks = {
        "momentum_score": "score",
        "abs20": "abs_mom",
        "base_signal_target": "base_target",
        "base_nav_for_dd": "base_nav",
        "base_dd_for_gate": "base_dd",
        "nav_decay_signal": "nav_gate",
        "signal_target": "signal_target",
        "execution_weight": "execution_weight",
    }
    baseline_max_error = {}
    for official_col, local_col in checks.items():
        a, b = official[official_col], old[local_col]
        if a.dtype == bool:
            diff = float((a != b).sum())
        else:
            diff = float((a - b).abs().max())
        baseline_max_error[official_col] = diff
        assert diff <= 1e-12, (official_col, diff)

    # The new static arm must reproduce the newly approved A-share sleeve on the same input.
    import sys

    sys.path.insert(0, r"D:\动量策略\A 股股指多头策略")
    import poe_cn_four_index_raw_momentum_combo_v1_3_bot as ashare

    cfg = next(item for item in ashare.SLEEVES if item.key == "zz500")
    ashare_curve = ashare.build_sleeve_curve(data, cfg)
    new_static = arms["new_static"]
    ashare_parity = {
        "score": float((ashare_curve["score"] - new_static["score"]).abs().max()),
        "abs40": float((ashare_curve["abs20"] - new_static["abs_mom"]).abs().max()),
        "base_target": float((ashare_curve["base_target_weight"] - new_static["base_target"]).abs().max()),
        "execution_weight": float((ashare_curve["final_weight"] - new_static["execution_weight"]).abs().max()),
        "strategy_ret": float((ashare_curve["strategy_ret"] - new_static["strategy_ret"]).abs().max()),
    }
    assert all(value <= 1e-12 for value in ashare_parity.values()), ashare_parity

    for name, frame in arms.items():
        assert (frame["nav"] <= frame["no_cost_nav_same_path"] + 1e-12).all(), name

    end = data.index[-1]
    windows = {"Full": None, **{f"{years}Y": end - pd.DateOffset(years=years) for years in (10, 5, 3, 1)}}
    rows = []
    for arm, frame in arms.items():
        for window, start in windows.items():
            rows.append({"arm": arm, "window": window, **metrics(frame, start)})
    summary = pd.DataFrame(rows)
    summary.to_csv(ROOT / "window_metrics.csv", index=False, float_format="%.12g")
    daily = pd.DataFrame(index=old.index)
    for name, frame in arms.items():
        for field in ("score", "abs_mom", "abs_on", "base_target", "base_dd", "nav_gate", "signal_target", "execution_weight", "strategy_ret", "nav"):
            daily[f"{name}__{field}"] = frame[field]
    daily.to_csv(ROOT / "daily_comparison.csv", index_label="date", float_format="%.12g")

    yearly_rows = []
    for year, dates in daily.groupby(daily.index.year):
        static_ret = float((1.0 + arms["new_static"].loc[dates.index, "strategy_ret"]).prod() - 1.0)
        debounce_ret = float((1.0 + arms["new_debounce_all"].loc[dates.index, "strategy_ret"]).prod() - 1.0)
        yearly_rows.append(
            {
                "year": year,
                "new_static_return": static_ret,
                "new_debounce_return": debounce_ret,
                "debounce_minus_static": debounce_ret - static_ret,
                "target_different_days": int(
                    (arms["new_static"].loc[dates.index, "signal_target"]
                     != arms["new_debounce_all"].loc[dates.index, "signal_target"]).sum()
                ),
            }
        )
    pd.DataFrame(yearly_rows).to_csv(ROOT / "yearly_comparison.csv", index=False, float_format="%.12g")

    static = arms["new_static"]
    debounced = arms["new_debounce_all"]
    delay_mask = static["base_target"].gt(debounced["base_target"])
    events = {
        "static_vs_debounce_target_different_days": int((static["signal_target"] != debounced["signal_target"]).sum()),
        "static_vs_debounce_execution_different_days": int((static["execution_weight"] != debounced["execution_weight"]).sum()),
        "debounce_target_lower_days": int((debounced["signal_target"] < static["signal_target"]).sum()),
        "debounce_target_higher_days_due_to_nav_feedback": int((debounced["signal_target"] > static["signal_target"]).sum()),
        "debounce_blocks_full_base_days": int(delay_mask.sum()),
        "debounce_blocks_full_base_days_1y": int(delay_mask.loc[delay_mask.index >= windows["1Y"]].sum()),
        "debounce_blocks_full_base_segments": int((delay_mask & ~delay_mask.shift(1, fill_value=False)).sum()),
        "new_static_vs_debounce_different_since_live_debounce_date": int(
            (static.loc["2026-09-16":, "signal_target"]
             != debounced.loc["2026-09-16":, "signal_target"]).sum()
        ),
        "latest_signal_date": str(static.index[-1].date()),
        "latest_static_target": float(static["signal_target"].iloc[-1]),
        "latest_debounce_target": float(debounced["signal_target"].iloc[-1]),
    }
    manifest = {
        "input": str(INPUT),
        "input_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "formal_source": str(Path(icim.__file__).resolve()),
        "formal_source_sha256": hashlib.sha256(Path(icim.__file__).read_bytes()).hexdigest(),
        "ashare_spec_hash": ashare.STRATEGY_SPEC_HASH,
        "formal_baseline_max_error": baseline_max_error,
        "new_static_ashare_max_error": ashare_parity,
        "events": events,
        "assumptions": "CSI500 price-index close-to-close research sleeve; T close signal -> next return row; 2% idle cash, 10bp one-way turnover, 6%/50% base-NAV defense; no futures basis, options, margin or fills",
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
