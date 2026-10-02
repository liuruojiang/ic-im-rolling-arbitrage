"""Research-only debounce scan for the IM v1.3 short-momentum sleeve.

The production baseline is reproduced through its actual v1.3 signal function.
Candidates make risk reduction immediate, but require sustained recovery before
re-adding exposure.  They are sleeve-only index-return proxies, not full IM
strategy performance: core, grid, Put, Call and the live ledger stay untouched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260915_ic_im_im_v1_3_r7_momentum_sleeve_"
    "im_score_and_abs20_recovery_gates_score_abs20_asymmetric_debounce"
)
SOURCE = ROOT / "poe_ic_im_mainline_v1_3_bot.py"
OHLCV = ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv"
ONE_WAY_COST = 0.0001
MOMENTUM_CAPITAL = 0.50


@dataclass(frozen=True)
class Variant:
    name: str
    score_reentry_days: int
    abs20_reentry_days: int
    score_reentry_threshold: float
    abs20_reentry_threshold: float


VARIANTS = (
    Variant("baseline_1d_zero", 1, 1, 0.0, 0.0),
    Variant("abs20_reentry_2d_zero", 1, 2, 0.0, 0.0),
    Variant("abs20_reentry_2d_p1", 1, 2, 0.0, 0.01),
    Variant("abs20_reentry_3d_p1", 1, 3, 0.0, 0.01),
    Variant("score_reentry_2d_zero", 2, 1, 0.0, 0.0),
    Variant("score_reentry_2d_p5", 2, 1, 5.0, 0.0),
    Variant("joint_reentry_2d_zero", 2, 2, 0.0, 0.0),
    Variant("joint_reentry_2d_abs_p1", 2, 2, 0.0, 0.01),
    # A deliberately coarse positive buffer: it tests a genuine threshold
    # rather than searching a fine local optimum.
    Variant("joint_reentry_2d_buffer", 2, 2, 5.0, 0.01),
)


def load_authority():
    spec = importlib.util.spec_from_file_location("im_debounce_authority", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recovery_state(value: pd.Series, *, days: int, threshold: float) -> pd.Series:
    """Immediate off at <=0; require N closes above threshold to turn on."""
    state = False
    streak = 0
    output: list[bool] = []
    for item in pd.to_numeric(value, errors="coerce"):
        if not np.isfinite(item) or item <= 0.0:
            state, streak = False, 0
        elif state:
            streak = 0
        elif item > threshold:
            streak += 1
            if streak >= days:
                state, streak = True, 0
        else:
            streak = 0
        output.append(state)
    return pd.Series(output, index=value.index, dtype=bool)


def build_variant(authority, ohlcv: pd.DataFrame, variant: Variant) -> pd.DataFrame:
    baseline = authority.v13_momentum_schedule("IM", ohlcv["close"], ohlcv).reset_index()
    baseline = baseline.rename(columns={baseline.columns[0]: "date"})
    score_ok = recovery_state(
        baseline["momentum_score"], days=variant.score_reentry_days,
        threshold=variant.score_reentry_threshold,
    )
    abs_ok = recovery_state(
        baseline["abs20"], days=variant.abs20_reentry_days,
        threshold=variant.abs20_reentry_threshold,
    )
    # Preserve volume and score-hot handling exactly after changing only the
    # recovery gates.  This maps states to 0 / 0.5 / 1 before the normal T+1 shift.
    target = score_ok.astype(float) * (0.5 + 0.5 * abs_ok.astype(float))
    target = target.where(baseline["volume_pass"].astype(bool), 0.0)
    hot = (baseline["momentum_score"] >= 150.0) & target.gt(0.0)
    target = target.where(~hot, 0.0)
    result = baseline.copy()
    result["score_reentry_pass"] = score_ok
    result["abs20_reentry_pass"] = abs_ok
    result["signal_target"] = target
    result["execution_weight"] = target.shift(1, fill_value=0.0)
    result["index_ret"] = result["close"].pct_change().fillna(0.0)
    result["turnover"] = result["execution_weight"].diff().abs().fillna(result["execution_weight"].abs())
    result["proxy_ret"] = (
        MOMENTUM_CAPITAL * result["execution_weight"] * result["index_ret"]
        - MOMENTUM_CAPITAL * ONE_WAY_COST * result["turnover"]
    )
    return result


def metrics(frame: pd.DataFrame) -> dict[str, float | int | str]:
    ret = frame["proxy_ret"].to_numpy(dtype=float)
    nav = np.cumprod(1.0 + ret)
    dd = nav / np.maximum.accumulate(np.r_[1.0, nav])[1:] - 1.0
    periods = len(frame)
    vol = float(np.std(ret, ddof=1) * math.sqrt(252.0)) if periods > 1 else math.nan
    ann = float(nav[-1] ** (252.0 / periods) - 1.0)
    return {
        "start": frame["date"].iloc[0].date().isoformat(), "end": frame["date"].iloc[-1].date().isoformat(),
        "rows": periods, "ann_return": ann, "ann_vol": vol,
        "sharpe": ann / vol if vol > 0 else math.nan, "max_drawdown": float(dd.min()),
        "avg_execution_weight": float(frame["execution_weight"].mean()),
        "turnover_total": float(frame["turnover"].sum()),
        "cost_total": float(MOMENTUM_CAPITAL * ONE_WAY_COST * frame["turnover"].sum()),
        "target_changes": int(frame["signal_target"].ne(frame["signal_target"].shift()).sum() - 1),
        "execution_changes": int(frame["execution_weight"].ne(frame["execution_weight"].shift()).sum() - 1),
    }


def main() -> None:
    authority = load_authority()
    raw = pd.read_csv(OHLCV, parse_dates=["date"]).sort_values("date").set_index("date")
    direct = authority.v13_momentum_schedule("IM", raw["close"], raw).reset_index()
    schedules = {item.name: build_variant(authority, raw, item) for item in VARIANTS}
    baseline = schedules["baseline_1d_zero"]
    parity = float(np.nanmax(np.abs(
        baseline["execution_weight"].to_numpy() - direct["execution_weight"].to_numpy()
    )))
    if parity > 1e-12:
        raise RuntimeError(f"Production baseline parity failed: {parity}")
    windows = {
        "full": baseline["date"].iloc[0], "last_10y": pd.Timestamp("2016-08-14"),
        "last_5y": pd.Timestamp("2021-08-14"), "last_3y": pd.Timestamp("2023-08-14"),
        "last_1y": pd.Timestamp("2025-08-14"),
    }
    long_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    daily: list[pd.DataFrame] = []
    base_by_window: dict[str, dict[str, object]] = {}
    for variant in VARIANTS:
        frame = schedules[variant.name]
        daily.append(frame.assign(candidate=variant.name))
        wide: dict[str, object] = {"candidate": variant.name, **asdict(variant)}
        for label, start in windows.items():
            sample = frame.loc[frame["date"].ge(start)].copy()
            row: dict[str, object] = {
                "candidate": variant.name, "window": label, "segment": label,
                **asdict(variant), **metrics(sample),
            }
            # Standard scan-contract aliases; retain the more descriptive
            # internal names alongside them for direct auditability.
            row["max_dd"] = row["max_drawdown"]
            row["sharpe_repo"] = row["sharpe"]
            if variant.name == "baseline_1d_zero":
                base_by_window[label] = row
            else:
                base = base_by_window[label]
                row["ann_return_delta"] = float(row["ann_return"]) - float(base["ann_return"])
                row["max_drawdown_delta"] = float(row["max_drawdown"]) - float(base["max_drawdown"])
                row["turnover_delta"] = float(row["turnover_total"]) - float(base["turnover_total"])
            long_rows.append(row)
            for field in ("ann_return", "max_drawdown", "turnover_total", "target_changes", "execution_changes"):
                wide[f"{field}_{label}"] = row[field]
            wide[f"max_dd_{label}"] = row["max_drawdown"]
        wide_rows.append(wide)
    summary = pd.DataFrame(long_rows)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8")
    pd.DataFrame(wide_rows).to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8")
    pd.concat(daily, ignore_index=True).to_csv(RUN / "daily_schedules.csv.gz", index=False, compression="gzip")
    churn = summary.pivot(index="candidate", columns="window", values=["target_changes", "execution_changes", "turnover_total"])
    churn.to_csv(RUN / "churn_comparison.csv", encoding="utf-8")
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "phase": "complete", "scan_type": "pre_registered_asymmetric_recovery_confirmation_scan",
        "baseline": {"candidate": "baseline_1d_zero", "definition": "official Score>0 and Abs20>0, volume/hot filters unchanged"},
        "candidate_grid": [asdict(item) for item in VARIANTS],
        "data_snapshot": {"path": str(OHLCV), "sha256": sha256(OHLCV), "start": str(raw.index.min().date()), "end": str(raw.index.max().date()), "adjustment": "vendor index OHLCV, unadjusted index-level series"},
        "cost_model": {"sleeve_only_proxy": "0.5*execution_weight*CSI1000 close return - 0.5*1bp*turnover", "excluded": "core, grid, Put, Call, cash, basis, slippage and live ledger"},
        "parity": {"official_v13_execution_weight_max_abs": parity},
        "decision": "research_only_no_parameter_promotion", "stability_label": "pending_result_interpretation",
        "outputs": {**meta["outputs"], "daily_schedules": str(RUN / "daily_schedules.csv.gz"), "churn_comparison": str(RUN / "churn_comparison.csv")},
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IM v1.3 短动量防抖扫描\n\n"
    record += "研究状态：仅研究，不改 r7、账本或下单。\n\n"
    record += "## Data\n\n"
    record += f"- 数据：`{OHLCV}`，{raw.index.min().date()} 至 {raw.index.max().date()}；指数 OHLCV。\n\n"
    record += "- 基线严格复现 `poe_ic_im_mainline_v1_3_bot.py:v13_momentum_schedule`，最大执行权重误差为 %.3e。\n" % parity
    record += "- 所有候选均保留负 Score/负 Abs20 的即时降风险；仅要求正向恢复连续确认后才重新加仓。\n"
    record += "- 收益是 IM 0.5 倍动量期货腿的透明指数代理，不能视为完整 IM r7 绩效。\n\n"
    record += "## 结果\n\n" + summary.to_markdown(index=False, floatfmt=".6f") + "\n"
    record += "\n## Decision\n\n- Decision: keep_baseline_research_only_pending_full_component_replay\n"
    record += "\n## Stability\n\n- Stability: evaluate_from_pre_registered_coarse_recovery_candidates\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_im_v13_short_momentum_debounce_v1.py\n")
    print(json.dumps({"run": str(RUN), "parity": parity, "rows": len(summary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
