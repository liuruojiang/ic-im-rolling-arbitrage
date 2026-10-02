"""Research-only scan for IM v1.3 volume-gate confirmation and hysteresis.

It deliberately changes only the volume gate that sits after the frozen
Score/Abs20 target.  The return series is a sleeve-only futures proxy, not an
IM v1.3 performance claim: it excludes the independently-priced Put, core,
grid and cash sleeves.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "poe_ic_im_mainline_v1_3_bot.py"
OHLCV = ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv"


@dataclass(frozen=True)
class Variant:
    name: str
    entry: float
    exit: float
    confirmation_days: int


VARIANTS = (
    Variant("baseline_085_1d", 0.85, 0.85, 1),
    Variant("confirm_085_2d", 0.85, 0.85, 2),
    Variant("hysteresis_090_080", 0.90, 0.80, 1),
    Variant("hysteresis_090_080_confirm2d", 0.90, 0.80, 2),
)


def load_authority():
    spec = importlib.util.spec_from_file_location("im_v13_scan_authority", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gated_state(ratio: pd.Series, *, entry: float, exit: float, days: int) -> pd.Series:
    """Stateful gate: N consecutive observations are needed to change state."""
    state = True  # Matches the authority's warm-up pass convention.
    streak = 0
    out: list[bool] = []
    for value in ratio:
        if not np.isfinite(value):
            out.append(state)
            continue
        desired = value >= (exit if state else entry)
        if desired == state:
            streak = 0
        else:
            streak += 1
            if streak >= days:
                state = desired
                streak = 0
        out.append(state)
    return pd.Series(out, index=ratio.index, dtype=bool)


def schedule(authority, ohlcv: pd.DataFrame, variant: Variant) -> pd.DataFrame:
    base = authority.v13_momentum_schedule("IM", ohlcv["close"], ohlcv)
    result = pd.DataFrame({"date": pd.to_datetime(ohlcv.index), "close": ohlcv["close"]})
    result["score"] = base["momentum_score"].to_numpy()
    result["abs20"] = base["abs20"].to_numpy()
    result["base_target"] = base["base_signal_target"].to_numpy()
    result["volume_ratio"] = base["volume_ratio"].to_numpy()
    if variant.name == "baseline_085_1d":
        # Preserve the authority's exact warm-up and first-observation behavior.
        gate = base["volume_pass"].astype(bool).reset_index(drop=True)
    else:
        gate = gated_state(result["volume_ratio"], entry=variant.entry, exit=variant.exit, days=variant.confirmation_days)
    # Match the production gate order: score/Abs20 first, then volume, then hot-score scaling.
    target = result["base_target"].where(gate, 0.0)
    hot = (result["score"] >= 150.0) & target.gt(0.0)
    result["volume_pass"] = gate
    if variant.name == "baseline_085_1d":
        result["signal_target"] = base["signal_target"].to_numpy()
        result["execution_weight"] = base["execution_weight"].to_numpy()
    else:
        result["signal_target"] = target.where(~hot, target * 0.5)
        result["execution_weight"] = result["signal_target"].shift(1, fill_value=0.0)
    result["index_ret"] = result["close"].pct_change().fillna(0.0)
    result["turnover"] = result["execution_weight"].diff().abs().fillna(result["execution_weight"].abs())
    # 0.5 sleeve exposure; 2bp per full-notional one-way futures turnover.  Proxy only.
    result["proxy_ret"] = 0.5 * result["execution_weight"] * result["index_ret"] - 0.5 * result["turnover"] * 0.0002
    return result


def metrics(frame: pd.DataFrame) -> dict[str, float]:
    nav = (1 + frame["proxy_ret"]).cumprod()
    dd = nav / nav.cummax() - 1
    n = len(frame)
    std = float(frame["proxy_ret"].std(ddof=1)) if n > 1 else 0.0
    return {
        "proxy_return": float(nav.iloc[-1] - 1),
        "proxy_cagr": float(nav.iloc[-1] ** (252 / n) - 1) if n else np.nan,
        "proxy_volatility": std * np.sqrt(252),
        "proxy_sharpe": float(frame["proxy_ret"].mean() / std * np.sqrt(252)) if std else 0.0,
        "proxy_max_drawdown": float(dd.min()),
        "avg_execution_weight": float(frame["execution_weight"].mean()),
        "turnover": float(frame["turnover"].sum()),
        "target_changes": int(frame["signal_target"].ne(frame["signal_target"].shift()).sum() - 1),
        "volume_gate_switches": int(frame["volume_pass"].ne(frame["volume_pass"].shift()).sum() - 1),
        "observations": int(n),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    authority = load_authority()
    raw = pd.read_csv(OHLCV, parse_dates=["date"]).sort_values("date")
    ohlcv = raw.set_index("date")
    schedules = {v.name: schedule(authority, ohlcv, v) for v in VARIANTS}
    baseline = schedules[VARIANTS[0].name]
    # Exact schedule parity is a hard acceptance gate for the unmodified parameter row.
    direct = authority.v13_momentum_schedule("IM", ohlcv["close"], ohlcv)
    parity = float(np.nanmax(np.abs(baseline["execution_weight"].to_numpy() - direct["execution_weight"].to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"Baseline execution-weight parity failed: {parity}")
    windows = {"full": raw["date"].min(), "last_10y": pd.Timestamp("2016-09-12"), "last_5y": pd.Timestamp("2021-09-12"), "last_3y": pd.Timestamp("2023-09-12"), "last_1y": pd.Timestamp("2025-09-12")}
    rows = []
    for variant in VARIANTS:
        frame = schedules[variant.name]
        frame.to_csv(args.out / f"schedule_{variant.name}.csv", index=False, encoding="utf-8")
        for label, start in windows.items():
            selected = frame.loc[frame["date"] >= start].copy()
            m = metrics(selected)
            total_turnover = m.pop("turnover")
            rows.append({
                "candidate": variant.name, "segment": label,
                "start": selected["date"].iloc[0].date().isoformat(),
                "end": selected["date"].iloc[-1].date().isoformat(),
                "rows": m.pop("observations"),
                "ann_return": m.pop("proxy_cagr"), "ann_vol": m.pop("proxy_volatility"),
                "sharpe_repo": m.pop("proxy_sharpe"), "max_dd": m.pop("proxy_max_drawdown"),
                "total_return_proxy": m.pop("proxy_return"),
                "avg_weight": m.pop("avg_execution_weight"),
                "avg_turnover": total_turnover / len(selected),
                "turnover_total": total_turnover,
                "target_changes": m.pop("target_changes"),
                "volume_gate_switches": m.pop("volume_gate_switches"),
                "cost_total": 0.5 * total_turnover * 0.0002,
                "entry_threshold": variant.entry, "exit_threshold": variant.exit,
                "confirmation_days": variant.confirmation_days,
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(args.out / "scan_summary.csv", index=False, encoding="utf-8")
    wide = []
    for variant in VARIANTS:
        part = summary.loc[summary["candidate"].eq(variant.name)].set_index("segment")
        row = {"candidate": variant.name, "entry_threshold": variant.entry, "exit_threshold": variant.exit, "confirmation_days": variant.confirmation_days}
        for segment in windows:
            row[f"ann_return_{segment}"] = part.loc[segment, "ann_return"]
            row[f"max_dd_{segment}"] = part.loc[segment, "max_dd"]
        row.update({"sharpe_repo_full": part.loc["full", "sharpe_repo"], "avg_weight_full": part.loc["full", "avg_weight"], "avg_turnover_full": part.loc["full", "avg_turnover"], "decision_hint": "research_only"})
        wide.append(row)
    pd.DataFrame(wide).to_csv(args.out / "window_metrics.csv", index=False, encoding="utf-8")
    (args.out / "scan_meta.json").write_text(json.dumps({
        "run_id": args.out.name,
        "created_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "project": "IC和IM滚动套利",
        "repo_root": str(ROOT), "entrypoint": "poe_ic_im_mainline_v1_3_bot.py:v13_momentum_schedule",
        "scan_type": "research_only_volume_gate_parameter_scan",
        "parameter_group": "volume_confirmation_hysteresis_v1",
        "baseline": {"candidate": "baseline_085_1d", "entry_threshold": 0.85, "exit_threshold": 0.85, "confirmation_days": 1},
        "candidate_grid": [v.name for v in VARIANTS],
        "data_snapshot": {"source": str(OHLCV), "start": raw["date"].min().date().isoformat(), "end": raw["date"].max().date().isoformat(), "cache_write_risk": "none_read_only"},
        "cost_model": {"formula": "0.5*execution_weight*CSI1000_return - 0.5*turnover*2bp", "scope": "sleeve_only_proxy; excludes core/grid/put/cash"},
        "scope": "research_only_sleeve_proxy_not_formal_im_strategy_performance",
        "baseline_parity_max_abs_error": parity,
        "source": str(OHLCV),
        "formula": "0.5*execution_weight*CSI1000_return - 0.5*turnover*2bp",
        "variants": [v.__dict__ for v in VARIANTS],
        "hot_score_rule": "unchanged_production_score_ge_150_scales_target_by_0.5",
        "execution": "T+1 prior close target",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
