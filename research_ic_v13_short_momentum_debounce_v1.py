"""Research-only IC v1.3 Score/Abs20 recovery-gate scan.

Risk reduction remains immediate.  The candidates only delay re-risking and
recompute the formal 6% base-NAV defence for every candidate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260915_ic_im_ic_v1_3_r7_momentum_sleeve_"
    "ic_score_and_abs20_with_nav_defense_score_abs20_asymmetric_debounce"
)
SOURCE = ROOT / "ic_mainline_v1_3.py"


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
)


def load_authority():
    spec = importlib.util.spec_from_file_location("ic_debounce_authority", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def recovery_state(value: pd.Series, *, days: int, threshold: float) -> pd.Series:
    """Switch off at <=0; switch back on only after N closes > threshold."""
    on, streak, out = False, 0, []
    for item in pd.to_numeric(value, errors="coerce"):
        if not np.isfinite(item) or item <= 0.0:
            on, streak = False, 0
        elif on:
            streak = 0
        elif item > threshold:
            streak += 1
            if streak >= days:
                on, streak = True, 0
        else:
            streak = 0
        out.append(on)
    return pd.Series(out, index=value.index, dtype=bool)


def build_variant(authority, ohlcv: pd.DataFrame, variant: Variant) -> pd.DataFrame:
    formal = authority.build_momentum_schedule(ohlcv)
    score_ok = recovery_state(formal["score"], days=variant.score_reentry_days,
                              threshold=variant.score_reentry_threshold)
    abs_ok = recovery_state(formal["abs20"], days=variant.abs20_reentry_days,
                            threshold=variant.abs20_reentry_threshold)
    raw_target = score_ok.astype(float) * (0.5 + 0.5 * abs_ok.astype(float))
    close = formal["close"].astype(float)
    base_execution = raw_target.shift(1, fill_value=0.0)
    raw_ret = close.pct_change().fillna(0.0)
    turnover = base_execution.diff().abs().fillna(base_execution.abs())
    policy = authority.MOMENTUM_POLICY
    daily_cash = (1.0 + policy.annual_cash_yield) ** (1.0 / policy.annualization_days) - 1.0
    cash_ret = (1.0 - base_execution).clip(0.0, 1.0) * daily_cash
    cash_ret.iloc[0] = 0.0
    base_ret = base_execution * raw_ret + cash_ret - policy.cost_rate * turnover
    base_nav = (1.0 + base_ret).cumprod()
    base_dd = base_nav / base_nav.cummax() - 1.0
    nav_defense = base_dd.le(-policy.nav_decay_threshold)
    scale = np.where(nav_defense, policy.nav_decay_scale, 1.0)
    target = raw_target * scale
    execution = target.shift(1, fill_value=0.0)
    final_turnover = execution.diff().abs().fillna(execution.abs())
    final_cash = (1.0 - execution).clip(0.0, 1.0) * daily_cash
    final_cash.iloc[0] = 0.0
    proxy_ret = execution * raw_ret + final_cash - policy.cost_rate * final_turnover
    return pd.DataFrame({
        "date": formal["date"], "close": close, "score": formal["score"], "abs20": formal["abs20"],
        "score_reentry_pass": score_ok, "abs20_reentry_pass": abs_ok,
        "base_momentum_signal_target": raw_target, "base_momentum_execution_weight": base_execution,
        "base_nav_for_dd": base_nav, "base_dd_for_gate": base_dd, "nav_decay_signal": nav_defense,
        "signal_target": target, "execution_weight": execution, "turnover": final_turnover,
        "proxy_ret": proxy_ret,
    })


def metrics(frame: pd.DataFrame) -> dict[str, object]:
    ret = frame["proxy_ret"].to_numpy(float)
    nav = np.cumprod(1.0 + ret)
    dd = nav / np.maximum.accumulate(nav) - 1.0
    n = len(frame)
    vol = float(np.std(ret, ddof=1) * math.sqrt(252.0))
    ann = float(nav[-1] ** (252.0 / n) - 1.0)
    return {
        "start": frame["date"].iloc[0].date().isoformat(), "end": frame["date"].iloc[-1].date().isoformat(),
        "rows": n, "ann_return": ann, "ann_vol": vol, "sharpe": ann / vol if vol else math.nan,
        "max_drawdown": float(dd.min()), "avg_execution_weight": float(frame["execution_weight"].mean()),
        "turnover_total": float(frame["turnover"].sum()), "cost_total": float(frame["turnover"].sum() * 0.001),
        "target_changes": int(frame["signal_target"].ne(frame["signal_target"].shift()).sum() - 1),
        "execution_changes": int(frame["execution_weight"].ne(frame["execution_weight"].shift()).sum() - 1),
        "nav_defense_days": int(frame["nav_decay_signal"].sum()),
    }


def run_stats(frame: pd.DataFrame, column: str) -> dict[str, int | float]:
    runs, length, active = [], 0, False
    for value in frame[column].astype(bool):
        if value:
            length += 1
            active = True
        elif active:
            runs.append(length)
            length, active = 0, False
    if active:
        runs.append(length)
    return {"runs": len(runs), "strict_lt3_runs": sum(x < 3 for x in runs),
            "le3_runs": sum(x <= 3 for x in runs), "median_run_days": float(np.median(runs)) if runs else math.nan,
            "on_days": int(frame[column].sum())}


def main() -> None:
    authority = load_authority()
    raw = pd.read_csv(authority.CSI500_OHLCV_PATH, parse_dates=["date"]).sort_values("date").set_index("date")
    candidates = {v.name: build_variant(authority, raw, v) for v in VARIANTS}
    official = authority.build_momentum_schedule(raw)
    baseline = candidates["baseline_1d_zero"]
    parity = max(
        float(np.abs(baseline["signal_target"] - official["momentum_signal_target"]).max()),
        float(np.abs(baseline["execution_weight"] - official["momentum_execution_weight"]).max()),
    )
    if parity > 1e-12:
        raise RuntimeError(f"Official baseline parity failed: {parity}")
    windows = {"full": baseline["date"].iloc[0], "last_10y": pd.Timestamp("2016-08-14"),
               "last_5y": pd.Timestamp("2021-08-14"), "last_3y": pd.Timestamp("2023-08-14"),
               "last_1y": pd.Timestamp("2025-08-14")}
    long, wide, daily, runs = [], [], [], []
    base_rows = {}
    for variant in VARIANTS:
        frame = candidates[variant.name]
        daily.append(frame.assign(candidate=variant.name))
        wide_row = {"candidate": variant.name, **asdict(variant)}
        for label, start in windows.items():
            row = {"candidate": variant.name, "window": label, "segment": label, **asdict(variant),
                   **metrics(frame.loc[frame.date.ge(start)].copy())}
            row["max_dd"], row["sharpe_repo"] = row["max_drawdown"], row["sharpe"]
            if variant.name == "baseline_1d_zero":
                base_rows[label] = row
            else:
                base = base_rows[label]
                for item in ("ann_return", "max_drawdown", "turnover_total", "target_changes"):
                    row[f"{item}_delta"] = float(row[item]) - float(base[item])
            long.append(row)
            for item in ("ann_return", "max_drawdown", "turnover_total", "target_changes", "execution_changes", "nav_defense_days"):
                wide_row[f"{item}_{label}"] = row[item]
            wide_row[f"max_dd_{label}"] = row["max_drawdown"]
        for column in ("abs20_reentry_pass", "signal_target"):
            tmp = frame.copy()
            if column == "signal_target":
                tmp[column] = tmp[column].eq(1.0)
            runs.append({"candidate": variant.name, "state": column, **run_stats(tmp, column)})
        wide.append(wide_row)
    summary = pd.DataFrame(long)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8")
    pd.DataFrame(wide).to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8")
    pd.concat(daily, ignore_index=True).to_csv(RUN / "daily_schedules.csv.gz", index=False, compression="gzip")
    pd.DataFrame(runs).to_csv(RUN / "short_run_stats.csv", index=False, encoding="utf-8")
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "phase": "complete", "scan_type": "pre_registered_asymmetric_recovery_confirmation_scan",
        "baseline": {"candidate": "baseline_1d_zero", "definition": "official IC Score>0 and Abs20>0 with dynamic 6% base-NAV defense"},
        "candidate_grid": [asdict(v) for v in VARIANTS],
        "data_snapshot": {"path": str(authority.CSI500_OHLCV_PATH), "sha256": sha256(authority.CSI500_OHLCV_PATH),
                          "start": str(raw.index.min().date()), "end": str(raw.index.max().date())},
        "cost_model": {"sleeve_only_proxy": "IC momentum sleeve index close return, 2% cash yield, 10bp one-way turnover cost; NAV defense recomputed per candidate",
                       "excluded": "IC futures core, grid, Put, basis, slippage beyond 10bp and live ledger"},
        "parity": {"official_v13_target_and_execution_max_abs": parity},
        "decision": "research_only_no_parameter_promotion", "stability_label": "pending_result_interpretation",
        "outputs": {**meta.get("outputs", {}), "daily_schedules": str(RUN / "daily_schedules.csv.gz"), "short_run_stats": str(RUN / "short_run_stats.csv")},
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC v1.3 Score / Abs20 防抖扫描\n\n研究状态：仅研究，不改 r7、账本或下单。\n\n"
    record += "## Data\n\n"
    record += f"- 数据：`{authority.CSI500_OHLCV_PATH}`，{raw.index.min().date()} 至 {raw.index.max().date()}。\n"
    record += f"- 基线严格复现 `ic_mainline_v1_3.py:build_momentum_schedule`，目标与执行权重最大误差 {parity:.3e}。\n"
    record += "- 每个候选单独重算正式 6% base-NAV 防御；负信号即时降风险，仅延迟恢复。\n\n"
    record += "## Results\n\n" + summary.to_markdown(index=False, floatfmt=".6f") + "\n"
    record += "\n## Decision\n\n- Decision: research_only_pending_full_component_cost_stress\n"
    record += "\n## Stability\n\n- Stability: coarse pre-registered candidates; inspect full and recent windows before promotion.\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_ic_v13_short_momentum_debounce_v1.py\n")
    print(json.dumps({"run": str(RUN), "parity": parity, "rows": len(summary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
