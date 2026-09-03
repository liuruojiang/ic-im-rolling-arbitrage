from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import build_ic_im_mainline_v1_3_fixed_performance as fixed


ROOT = Path(__file__).resolve().parent
VERSION = "im_v13_put_coverage_scope_ablation_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_put_coverage_scope"
)
FIXED_DAILY = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
REAL_START = pd.Timestamp("2022-07-22")
MO_CONTRACT_SIDE_COST = 0.00005

WINDOWS: dict[str, pd.Timestamp | None] = {
    "full": None,
    "last_10y": pd.Timestamp("2026-08-14") - pd.DateOffset(years=10),
    "last_5y": pd.Timestamp("2026-08-14") - pd.DateOffset(years=5),
    "last_3y": pd.Timestamp("2026-08-14") - pd.DateOffset(years=3),
    "last_1y": pd.Timestamp("2026-08-14") - pd.DateOffset(years=1),
    "real_im_mo": REAL_START,
}

CANDIDATES = (
    "no_put",
    "core_only_current",
    "core_plus_momentum",
    "core_plus_grid",
    "core_plus_momentum_plus_grid",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_spec() -> str:
    expected = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(SPEC)
    if actual != expected:
        raise RuntimeError(f"Specification hash mismatch: {actual} != {expected}")
    return actual


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def load_components() -> pd.DataFrame:
    components = fixed._load_im_components()
    target = pd.read_csv(fixed.IM_TARGET, parse_dates=["date"])
    columns = [
        "date",
        "momentum_execution_weight",
        "grid_held_eod",
        "total_im_units",
    ]
    frame = components.merge(target[columns], on="date", validate="one_to_one")
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise RuntimeError("IM component dates are not unique and increasing")
    if frame["date"].iloc[-1] != pd.Timestamp("2026-08-14"):
        raise RuntimeError("Unexpected common end date")
    weight = frame["momentum_execution_weight"].astype(float)
    grid = frame["grid_held_eod"].astype(float)
    units = 0.5 + 0.5 * weight + grid
    parity = float((units - frame["total_im_units_y"].astype(float)).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"IM total-unit parity failed: {parity}")
    frame["momentum_units"] = 0.5 * weight
    frame["grid_units"] = grid
    frame["total_units_rebuilt"] = units
    frame["data_layer"] = np.where(frame["date"].lt(REAL_START), "model", "real")
    return frame


def reconstruct_cost(
    dates: pd.Series, contract: pd.Series, target_qty: pd.Series
) -> tuple[pd.Series, pd.Series]:
    contracts = contract.fillna("").astype(str).where(target_qty.gt(0), "")
    qty = target_qty.fillna(0.0).astype(float)
    costs = np.zeros(len(qty), dtype=float)
    sides = np.zeros(len(qty), dtype=float)
    previous_contract = ""
    previous_qty = 0.0
    for i, (date, current_contract, current_qty) in enumerate(
        zip(dates, contracts, qty, strict=True)
    ):
        if i == 0 or pd.Timestamp(date) == REAL_START:
            previous_contract = ""
            previous_qty = 0.0
        if current_contract == previous_contract:
            traded = abs(float(current_qty) - previous_qty)
        else:
            traded = previous_qty + float(current_qty)
        sides[i] = traded
        costs[i] = traded * MO_CONTRACT_SIDE_COST
        previous_contract = current_contract
        previous_qty = float(current_qty)
    return pd.Series(costs, index=target_qty.index), pd.Series(sides, index=target_qty.index)


def coverage_scales(frame: pd.DataFrame) -> dict[str, pd.Series]:
    zero = pd.Series(0.0, index=frame.index)
    core = pd.Series(0.5, index=frame.index)
    momentum = frame["momentum_units"].astype(float)
    grid = frame["grid_units"].astype(float)
    return {
        "no_put": zero,
        "core_only_current": core,
        "core_plus_momentum": core + momentum,
        "core_plus_grid": core + grid,
        "core_plus_momentum_plus_grid": core + momentum + grid,
    }


def build_daily(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    weight = frame["momentum_execution_weight"].astype(float)
    units = frame["total_units_rebuilt"].astype(float)
    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    real_rows = frame.index[frame["date"].eq(REAL_START)]
    if len(real_rows) != 1:
        raise RuntimeError("Real IM/MO start date is missing or duplicated")
    turnover.loc[int(real_rows[0])] = abs(float(weight.loc[int(real_rows[0])]))
    momentum_cost_full = (
        fixed.im_proxy.ONE_WAY_COST * turnover
        + 2.0
        * fixed.im_proxy.ONE_WAY_COST
        * weight
        * frame["roll_event"].astype(float)
    )
    base_gross = frame["base_gross_ret"].astype(float) + frame["base_basis_ret"].astype(float)
    overlay_gross = frame["overlay_gross_ret"].astype(float) + frame["overlay_basis_ret"].astype(float)
    futures_gross = (0.5 + 0.5 * weight) * base_gross + overlay_gross
    futures_cost = (
        0.5 * frame["base_futures_cost_rate"].astype(float)
        + 0.5 * momentum_cost_full
        + frame["overlay_cost_rate"].astype(float)
    )
    call_pnl = 0.5 * frame["call_pnl_ret"].astype(float)
    call_cost = 0.5 * frame["call_cost_rate"].astype(float)
    call_margin = 0.5 * frame["call_margin_fraction"].astype(float)

    parent_cost_rebuilt, parent_sides = reconstruct_cost(
        frame["date"], frame["put_contract"], frame["put_qty"].astype(float)
    )
    parent_cost_error = float(
        (parent_cost_rebuilt - frame["put_cost_rate"].astype(float)).abs().max()
    )
    if parent_cost_error > 1e-12:
        raise RuntimeError(f"Parent Put cost reconstruction failed: {parent_cost_error}")

    outputs: list[pd.DataFrame] = []
    for candidate, scale in coverage_scales(frame).items():
        put_qty = frame["put_qty"].astype(float) * scale
        put_cost, put_sides = reconstruct_cost(frame["date"], frame["put_contract"], put_qty)
        put_pnl = frame["put_pnl_ret"].astype(float) * scale
        put_mark = frame["put_mark_fraction"].astype(float) * scale
        pre_cash = (
            (1.0 + futures_gross + put_pnl + call_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            * (1.0 - call_cost)
            - 1.0
        )
        cash_raw = 1.0 - fixed.im_proxy.MARGIN_BUFFER_RATE * units - put_mark - call_margin
        # Keep negative cash as an explicit financing diagnostic.  It earns the
        # same signed 3% rate (therefore pays interest when negative), but the
        # pre-registered feasibility check automatically rejects the path.
        cash = cash_raw
        ret = pre_cash + cash * fixed.im_proxy.CASH_DAILY_RETURN
        if not np.isfinite(ret).all() or ret.le(-1.0).any():
            raise RuntimeError(f"Invalid return path for {candidate}")
        out = pd.DataFrame(
            {
                "date": frame["date"],
                "candidate": candidate,
                "ret": ret,
                "cash_weight": cash,
                "total_units": units,
                "coverage_scale": scale,
                "put_qty_normalized": put_qty,
                "put_pnl_ret": put_pnl,
                "put_cost_rate": put_cost,
                "put_trade_sides": put_sides,
                "put_mark_fraction": put_mark,
                "put_contract": frame["put_contract"].fillna(""),
                "call_pnl_ret": call_pnl,
                "call_cost_rate": call_cost,
                "futures_gross_ret": futures_gross,
                "futures_cost_rate": futures_cost,
                "data_layer": frame["data_layer"],
                "momentum_units": frame["momentum_units"],
                "grid_units": frame["grid_units"],
            }
        )
        out["nav"] = (1.0 + out["ret"]).cumprod()
        out["drawdown"] = out["nav"] / out["nav"].cummax() - 1.0
        outputs.append(out)

    daily = pd.concat(outputs, ignore_index=True)
    current = daily[daily["candidate"].eq("core_only_current")].reset_index(drop=True)
    frozen = pd.read_csv(FIXED_DAILY, parse_dates=["date"])
    if not current["date"].equals(frozen["date"]):
        raise RuntimeError("Current candidate date index differs from fixed-performance v5")
    parity = {
        "parent_put_cost_reconstruction_max_abs": parent_cost_error,
        "parent_put_trade_sides": float(parent_sides.sum()),
        "current_ret_vs_v5_max_abs": float((current["ret"] - frozen["ret"]).abs().max()),
        "current_cash_vs_v5_max_abs": float(
            (current["cash_weight"] - frozen["cash_weight"]).abs().max()
        ),
        "current_put_qty_vs_v5_max_abs": float(
            (current["put_qty_normalized"] - frozen["put_qty_normalized"]).abs().max()
        ),
    }
    if max(
        parity["current_ret_vs_v5_max_abs"],
        parity["current_cash_vs_v5_max_abs"],
        parity["current_put_qty_vs_v5_max_abs"],
    ) > 1e-12:
        raise RuntimeError(f"Fixed-performance v5 baseline parity failed: {parity}")
    return daily, parity


def rolling_worst(ret: pd.Series, days: int) -> float:
    values = np.log1p(ret.astype(float)).rolling(days).sum()
    return float(np.expm1(values.min()))


def metric_values(sample: pd.DataFrame) -> dict[str, float | int | str]:
    ret = sample["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    trough_index = int(drawdown.idxmin())
    peak_index = int(nav.loc[:trough_index].idxmax())
    return {
        "rows": int(len(sample)),
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe_repo": float(ret.mean()) / std * math.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(drawdown.min()),
        "final_nav": float(nav.iloc[-1]),
        "worst_1d": float(ret.min()),
        "worst_5d": rolling_worst(ret, 5),
        "worst_20d": rolling_worst(ret, 20),
        "worst_60d": rolling_worst(ret, 60),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "put_trade_sides": float(sample["put_trade_sides"].sum()),
        "avg_put_qty": float(sample["put_qty_normalized"].mean()),
        "max_put_qty": float(sample["put_qty_normalized"].max()),
        "put_active_days": int(sample["put_qty_normalized"].gt(0).sum()),
        "min_cash_weight": float(sample["cash_weight"].min()),
        "dd_peak": sample.loc[peak_index, "date"].date().isoformat(),
        "dd_trough": sample.loc[trough_index, "date"].date().isoformat(),
    }


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        block = daily[daily["candidate"].eq(candidate)].reset_index(drop=True)
        for segment, start in WINDOWS.items():
            sample = block.copy() if start is None else block[block["date"].ge(start)].copy()
            if start is not None:
                sample = sample.iloc[1:].copy()
            sample = sample.reset_index(drop=True)
            rows.append({"candidate": candidate, "segment": segment, **metric_values(sample)})
    long = pd.DataFrame(rows)
    baseline = long[long["candidate"].eq("core_only_current")].set_index("segment")
    for index, row in long.iterrows():
        base = baseline.loc[row["segment"]]
        long.loc[index, "ann_return_delta_vs_current"] = row["ann_return"] - base["ann_return"]
        long.loc[index, "max_dd_improvement_vs_current"] = row["max_dd"] - base["max_dd"]
        long.loc[index, "sharpe_delta_vs_current"] = row["sharpe_repo"] - base["sharpe_repo"]
        long.loc[index, "worst_20d_delta_vs_current"] = row["worst_20d"] - base["worst_20d"]
        long.loc[index, "worst_60d_delta_vs_current"] = row["worst_60d"] - base["worst_60d"]

    required = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
    wide_rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        candidate_rows = long[long["candidate"].eq(candidate)].set_index("segment")
        record: dict[str, object] = {"candidate": candidate}
        for segment in required:
            for field in (
                "ann_return",
                "ann_vol",
                "sharpe_repo",
                "max_dd",
                "worst_20d",
                "worst_60d",
                "put_cost_total",
                "max_put_qty",
                "min_cash_weight",
            ):
                record[f"{field}_{segment}"] = candidate_rows.loc[segment, field]
        real = candidate_rows.loc["real_im_mo"]
        for field in (
            "ann_return",
            "ann_vol",
            "sharpe_repo",
            "max_dd",
            "worst_20d",
            "worst_60d",
            "put_cost_total",
            "max_put_qty",
            "min_cash_weight",
        ):
            record[f"{field}_real_im_mo"] = real[field]
        wide_rows.append(record)
    return long, pd.DataFrame(wide_rows)


def build_attribution(daily: pd.DataFrame, long: pd.DataFrame) -> pd.DataFrame:
    real = daily[daily["date"].gt(REAL_START)].copy()
    pivot = real.pivot(index="date", columns="candidate", values="ret")
    baseline = pivot["core_only_current"]
    baseline_real = long[
        long["candidate"].eq("core_only_current") & long["segment"].eq("real_im_mo")
    ].iloc[0]
    event_mask = pivot.index.to_series().between(
        pd.Timestamp(baseline_real["dd_peak"]), pd.Timestamp(baseline_real["dd_trough"])
    )
    rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        delta = pivot[candidate] - baseline
        positive = delta.clip(lower=0.0)
        positive_sum = float(positive.sum())
        top1_share = float(positive.max() / positive_sum) if positive_sum > 0 else 0.0
        rows.append(
            {
                "candidate": candidate,
                "real_daily_delta_sum_vs_current": float(delta.sum()),
                "best_delta_day": delta.idxmax().date().isoformat(),
                "best_daily_delta_vs_current": float(delta.max()),
                "top1_share_of_positive_daily_delta": top1_share,
                "positive_delta_days": int(delta.gt(0).sum()),
                "current_max_dd_event_compound_return": float(
                    (1.0 + pivot.loc[event_mask, candidate]).prod() - 1.0
                ),
            }
        )
    return pd.DataFrame(rows)


def evaluate_decision(
    long: pd.DataFrame, attribution: pd.DataFrame
) -> tuple[pd.DataFrame, str, str]:
    real = long[long["segment"].eq("real_im_mo")].set_index("candidate")
    baseline = real.loc["core_only_current"]
    attr = attribution.set_index("candidate")
    rows: list[dict[str, object]] = []
    for candidate in CANDIDATES[2:]:
        row = real.loc[candidate]
        checks = {
            "dd_improvement_ge_1pp": row["max_dd"] - baseline["max_dd"] >= 0.01 - 1e-12,
            "cagr_loss_le_0_5pp": row["ann_return"] - baseline["ann_return"] >= -0.005 - 1e-12,
            "sharpe_not_lower": row["sharpe_repo"] >= baseline["sharpe_repo"] - 1e-12,
            "worst_20d_not_worse": row["worst_20d"] >= baseline["worst_20d"] - 1e-12,
            "worst_60d_not_worse": row["worst_60d"] >= baseline["worst_60d"] - 1e-12,
            "cash_nonnegative": row["min_cash_weight"] >= -1e-12,
            "not_single_day_dominated": attr.loc[candidate, "top1_share_of_positive_daily_delta"] < 0.5,
        }
        rows.append(
            {
                "candidate": candidate,
                **checks,
                "all_preregistered_checks_pass": all(checks.values()),
            }
        )
    checks = pd.DataFrame(rows)
    momentum = bool(
        checks.loc[checks["candidate"].eq("core_plus_momentum"), "all_preregistered_checks_pass"].iloc[0]
    )
    grid = bool(
        checks.loc[checks["candidate"].eq("core_plus_grid"), "all_preregistered_checks_pass"].iloc[0]
    )
    all_cover = bool(
        checks.loc[
            checks["candidate"].eq("core_plus_momentum_plus_grid"),
            "all_preregistered_checks_pass",
        ].iloc[0]
    )
    if momentum and grid:
        decision = "research_supports_covering_both_momentum_and_grid_not_live_approved"
        label = "broad_real_sample_support"
    elif momentum:
        decision = "research_supports_momentum_put_only_keep_grid_unprotected_not_live_approved"
        label = "sleeve_specific_support"
    elif grid:
        decision = "research_supports_grid_put_only_keep_momentum_unprotected_not_live_approved"
        label = "sleeve_specific_support"
    elif all_cover:
        decision = "interaction_only_evidence_keep_current_pending_more_tests_not_live_approved"
        label = "interaction_only_unstable"
    else:
        decision = "keep_current_core_only_put_research_conclusion_not_live_approved"
        label = "no_incremental_support"
    return checks, decision, label


def pct(value: object) -> str:
    return f"{100.0 * float(value):.2f}%"


def write_record(
    long: pd.DataFrame,
    checks: pd.DataFrame,
    parity: dict[str, float | int | str],
    decision: str,
    stability_label: str,
    elapsed: float,
) -> None:
    table = [
        "|候选|全样本 CAGR / MaxDD|近10年|近5年|近3年|近1年|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "no_put": "无Put（诊断）",
        "core_only_current": "仅核心仓（当前）",
        "core_plus_momentum": "核心+动量",
        "core_plus_grid": "核心+网格",
        "core_plus_momentum_plus_grid": "核心+动量+网格",
    }
    for candidate in CANDIDATES:
        rows = long[long["candidate"].eq(candidate)].set_index("segment")
        cells = [
            f"{pct(rows.loc[segment, 'ann_return'])} / {pct(rows.loc[segment, 'max_dd'])}"
            for segment in ("full", "last_10y", "last_5y", "last_3y", "last_1y")
        ]
        table.append(f"|{labels[candidate]}|" + "|".join(cells) + "|")
    real_table = [
        "|候选|CAGR|Sharpe|MaxDD|最差20日|最差60日|Put成本合计|最大Put张数|最低现金|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    real = long[long["segment"].eq("real_im_mo")].set_index("candidate")
    for candidate in CANDIDATES:
        row = real.loc[candidate]
        real_table.append(
            f"|{labels[candidate]}|{pct(row.ann_return)}|{row.sharpe_repo:.2f}|"
            f"{pct(row.max_dd)}|{pct(row.worst_20d)}|{pct(row.worst_60d)}|"
            f"{pct(row.put_cost_total)}|{row.max_put_qty:.2f}|{pct(row.min_cash_weight)}|"
        )
    check_table = [
        "|候选|回撤改善≥1pp|CAGR损失≤0.5pp|Sharpe不降|20日不差|60日不差|非单日驱动|全部通过|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in checks.itertuples(index=False):
        values = [
            row.dd_improvement_ge_1pp,
            row.cagr_loss_le_0_5pp,
            row.sharpe_not_lower,
            row.worst_20d_not_worse,
            row.worst_60d_not_worse,
            row.not_single_day_dominated,
            row.all_preregistered_checks_pass,
        ]
        check_table.append(
            f"|{labels[row.candidate]}|" + "|".join("是" if value else "否" for value in values) + "|"
        )
    text = f"""# IM v1.3 Put 覆盖范围消融 v1

## Run Metadata

- 状态：研究完成；不是冻结主线，不是实盘授权。
- 版本：`{VERSION}`。
- 时区：Asia/Shanghai；运行耗时：{elapsed:.2f} 秒。
- 决策：`{decision}`；稳定性：`{stability_label}`。

## Research Question

在 IM v1.3 其他机制完全不变时，动量仓和网格仓是否也需要采用与核心仓同一估值档位、合约、期限和行权价的动态 Put 保护？

## Implementation Anchor

- 基准为 `build_ic_im_mainline_v1_3_fixed_performance.py::build_im` 与 v5 定值曲线。
- 当前候选只改变 Put 覆盖单位数；期货、动量权重、网格、Call、30%保证金/缓冲和现金收益不变。
- 当前基准逐日收益复现误差：`{parity['current_ret_vs_v5_max_abs']:.3e}`；现金误差：`{parity['current_cash_vs_v5_max_abs']:.3e}`；Put数量误差：`{parity['current_put_qty_vs_v5_max_abs']:.3e}`。

## Data Snapshot

- 共同样本：2015-04-16 至 2026-08-14，共 2756 个交易日。
- 真实 IM/MO：2022-07-22 起；此前为理论 Put 与模型贴水层，含未来信息，只作参考。
- 来源哈希与完整候选逐日路径见 `run_manifest.json` 和 `daily_outputs/coverage_candidates.csv.gz`。

## Cost and Execution Assumptions

- Put 合约与 T+1 收盘执行状态沿用父路径；每归一化 MO 合约单边成本 0.005%。
- 候选 Put 成本按合约/数量变化重新计边；父路径成本复原误差 `{parity['parent_put_cost_reconstruction_max_abs']:.3e}`。
- 未计盘口价差、冲击、容量、涨跌停、动态保证金和整数张映射误差。

## Runtime Override Plan

- 无运行时覆盖；不修改生产参数、Poe、账本、日报或冻结输出。
- 本次只新增规格、研究脚本和独立扫描目录。

## Commands

- `python -X utf8 scan_im_v13_put_coverage_scope_v1.py`
- `python D:\\Codex\\home\\skills\\quant-param-scan\\scripts\\finalize_quant_param_scan_run.py <run_folder> --decision \"{decision}\" --stability-label \"{stability_label}\"`
- `python D:\\Codex\\home\\skills\\quant-param-scan\\scripts\\check_quant_param_scan_artifacts.py --phase complete --strict <run_folder>`

## Output Files

- `scan_summary.csv`：长表，含所有窗口和真实段。
- `window_metrics.csv`：候选宽表。
- `daily_outputs/coverage_candidates.csv.gz`：逐日可审计路径。
- `decision_checks.csv`、`event_attribution.csv`、`parity_checks.json`、`run_manifest.json`。

## Full-Sample Results

每格为 CAGR / MaxDD；Full/10Y/5Y 混有不可执行的模型层。

{chr(10).join(table)}

## Window Results

真实 IM/MO 段是决策主证据；其首日作为切换基准，绩效从下一交易日计。

{chr(10).join(real_table)}

## Stability Classification

{chr(10).join(check_table)}

## Decision

`{decision}`。

规则判定只回答“新增保护是否有足够真实段证据”，不等于任何下单建议。若新增保护没有全部通过，维持当前仅核心仓 Put 的研究结论；若通过，也仍需独立整数合约、价差/冲击和样本外验证后另行预注册。

## User-Facing Summary

请以真实段相对“仅核心仓”的 CAGR、Sharpe、最大回撤和最差20/60日变化为主；上市前全样本只能帮助识别模型脆弱性，不能覆盖真实成交证据。
"""
    (RUN / "record.md").write_text(text, encoding="utf-8")


def update_meta(
    spec_hash: str,
    decision: str,
    stability_label: str,
    parity: dict[str, float | int | str],
    elapsed: float,
) -> None:
    path = RUN / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(
        {
            "entrypoint": Path(__file__).name,
            "scan_type": "pre_registered_discrete_ablation",
            "baseline": {"candidate": "core_only_current", "fixed_output": str(FIXED_DAILY.relative_to(ROOT))},
            "candidate_grid": list(CANDIDATES),
            "data_snapshot": {
                "start": "2015-04-16",
                "end": "2026-08-14",
                "real_im_mo_start": "2022-07-22",
                "pre_real_layer": "theoretical_put_plus_model_avg_basis_with_lookahead_reference_only",
            },
            "cost_model": {
                "mo_normalized_contract_one_way": MO_CONTRACT_SIDE_COST,
                "futures_and_call": "unchanged_from_ic_im_mainline_v1_3_fixed_performance_v5",
                "cash_annual": 0.03,
                "margin_buffer_per_1x_im": 0.30,
                "bid_ask_impact": "excluded",
            },
            "parity_check": parity,
            "spec_sha256": spec_hash,
            "decision": decision,
            "stability_label": stability_label,
            "elapsed_sec": elapsed,
            "warnings": [
                "2015-2022 prelisting Put and basis layer is reference-only and contains model/lookahead risk",
                "normalized fractional MO quantities are not integer-contract execution proof",
                "bid-ask spread, impact, capacity, dynamic margin, and price limits are excluded",
            ],
        }
    )
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    spec_hash = verify_spec()
    required = [RUN / "scan_meta.json", FIXED_DAILY, fixed.IM_COMPONENTS, fixed.IM_TARGET]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(missing)
    for path in (
        RUN / "scan_summary.csv",
        RUN / "window_metrics.csv",
        RUN / "daily_outputs" / "coverage_candidates.csv.gz",
    ):
        if path.exists():
            raise FileExistsError(f"Immutable scan output already exists: {path}")

    frame = load_components()
    daily, parity = build_daily(frame)
    long, wide = build_metrics(daily)
    attribution = build_attribution(daily, long)
    checks, decision, stability_label = evaluate_decision(long, attribution)
    elapsed = time.perf_counter() - started

    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(parents=True, exist_ok=False)
    daily.to_csv(daily_dir / "coverage_candidates.csv.gz", index=False, compression="gzip")
    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    attribution.to_csv(RUN / "event_attribution.csv", index=False)
    checks.to_csv(RUN / "decision_checks.csv", index=False)
    (RUN / "parity_checks.json").write_text(
        json.dumps(parity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sources = [SPEC, SPEC_HASH, FIXED_DAILY, fixed.IM_COMPONENTS, fixed.IM_TARGET]
    manifest = {
        "version": VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "research_only_not_live_approved",
        "spec_sha256": spec_hash,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "decision": decision,
        "stability_label": stability_label,
    }
    (RUN / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_record(long, checks, parity, decision, stability_label, elapsed)
    update_meta(spec_hash, decision, stability_label, parity, elapsed)
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] cwd={ROOT}\n"
            f"python -X utf8 {Path(__file__).name}\n"
            f"elapsed_sec={elapsed:.3f}\n"
        )
    print(long[long["segment"].eq("real_im_mo")].to_string(index=False))
    print(checks.to_string(index=False))
    print(json.dumps({"decision": decision, "stability_label": stability_label, "parity": parity}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
