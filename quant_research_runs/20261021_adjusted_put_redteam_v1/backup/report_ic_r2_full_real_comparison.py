#!/usr/bin/env python
"""Render every IC R2 candidate against R2-off for full and real-only data."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260912_ic_im_v13_r7_r2_current_f_base_width_scan"
OUT = RUN / "reports"


def _metrics_for_window(metrics: pd.DataFrame, window: str, prefix: str) -> pd.DataFrame:
    result = metrics.loc[metrics["window"].eq(window)].copy()
    if result["candidate"].duplicated().any():
        raise RuntimeError(f"duplicate IC candidate in {window}")
    return result[
        ["candidate", "start", "end", "periods", "ann_return", "max_drawdown"]
    ].rename(
        columns={
            "start": f"{prefix}_start",
            "end": f"{prefix}_end",
            "periods": f"{prefix}_rows",
            "ann_return": f"{prefix}_ann_return",
            "max_drawdown": f"{prefix}_max_drawdown",
        }
    )


def _matrix(data: pd.DataFrame, column: str) -> pd.DataFrame:
    result = data.loc[data["candidate"].ne("r2_off")].pivot(
        index="r2_threshold", columns="r2_window", values=column
    )
    return result.sort_index().sort_index(axis=1)


def _plot(data: pd.DataFrame, baseline: pd.Series) -> Path:
    panels = [
        ("full_ann_return_delta_pp", f"Full annual return Δ pp\nR2-off = {baseline['full_ann_return']:.2%}"),
        ("full_max_dd_delta_pp", f"Full max drawdown Δ pp\nR2-off = {baseline['full_max_drawdown']:.2%}"),
        ("real_ann_return_delta_pp", f"Real-only annual return Δ pp\nR2-off = {baseline['real_ann_return']:.2%}"),
        ("real_max_dd_delta_pp", f"Real-only max drawdown Δ pp\nR2-off = {baseline['real_max_drawdown']:.2%}"),
    ]
    arrays = [_matrix(data, column) for column, _ in panels]
    cap = max(float(np.nanmax(np.abs(item.to_numpy(dtype=float)))) for item in arrays)
    cap = max(cap, 0.05)
    norm = TwoSlopeNorm(vmin=-cap, vcenter=0.0, vmax=cap)
    fig, axes = plt.subplots(2, 2, figsize=(15, 12), sharex=True, sharey=True, facecolor="white")
    image = None
    for axis, matrix, (_, title) in zip(axes.ravel(), arrays, panels):
        image = axis.imshow(matrix.to_numpy(dtype=float), origin="lower", aspect="auto", cmap="RdYlGn", norm=norm)
        axis.set_title(title, fontsize=11, weight="bold")
        axis.set_xticks(range(len(matrix.columns)), [str(int(value)) for value in matrix.columns])
        y_ticks = list(range(0, len(matrix.index), 2))
        axis.set_yticks(y_ticks, [f"{matrix.index[i]:.3f}" for i in y_ticks])
        axis.set_xlabel("R2 regression window (trading days)")
        axis.set_ylabel("R2 threshold")
    assert image is not None
    fig.subplots_adjust(left=0.07, right=0.84, bottom=0.12, top=0.88, hspace=0.20, wspace=0.10)
    color_axis = fig.add_axes([0.88, 0.24, 0.018, 0.52])
    colorbar = fig.colorbar(image, cax=color_axis)
    colorbar.set_label("change versus R2-off (percentage points; green is better)")
    fig.suptitle("IC v1.3-r7 F base — all R2 candidates, full vs real-only", fontsize=15, weight="bold", y=0.985)
    fig.text(0.5, 0.012, "Full: 2015-04-16–2026-09-11 (model extension + real); real-only: 2022-09-19–2026-09-11.\nR2 changes only the 0.5 momentum F sleeve; grid/Put/Call are excluded.", ha="center", fontsize=9, color="#4b5563")
    path = OUT / "ic_r2_full_real_all_candidates_heatmap.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    if not RUN.is_dir():
        raise FileNotFoundError(RUN)
    OUT.mkdir(parents=True, exist_ok=True)
    summary = pd.read_csv(RUN / "candidate_summary_detail.csv")
    metrics = pd.read_csv(RUN / "window_metrics_detail.csv")
    summary = summary.loc[summary["product"].eq("IC")].copy()
    metrics = metrics.loc[metrics["product"].eq("IC")].copy()
    full = _metrics_for_window(metrics, "full", "full")
    real = _metrics_for_window(metrics, "real_only", "real")
    # The scan summary already carries full-window values for the pass map;
    # take the canonical absolute metrics from the detailed window table so
    # the comparison has one unambiguous full/real column set.
    summary = summary.drop(columns=["full_ann_return", "full_max_drawdown"])
    comparison = summary.merge(full, on="candidate", validate="one_to_one").merge(real, on="candidate", validate="one_to_one")
    baseline = comparison.loc[comparison["candidate"].eq("r2_off")].iloc[0]
    if len(comparison) != 232 or comparison["candidate"].duplicated().any():
        raise RuntimeError(f"Expected 232 unique IC rows including R2-off, got {len(comparison)}")
    comparison["full_ann_return_delta_pp"] = 100.0 * (comparison["full_ann_return"] - baseline["full_ann_return"])
    comparison["full_max_dd_delta_pp"] = 100.0 * (comparison["full_max_drawdown"] - baseline["full_max_drawdown"])
    comparison["real_ann_return_delta_pp"] = 100.0 * (comparison["real_ann_return"] - baseline["real_ann_return"])
    comparison["real_max_dd_delta_pp"] = 100.0 * (comparison["real_max_drawdown"] - baseline["real_max_drawdown"])
    comparison["full_pass_either"] = (
        comparison["full_ann_return_delta_pp"].gt(1e-10)
        | comparison["full_max_dd_delta_pp"].gt(1e-10)
    )
    comparison["real_pass_either"] = (
        comparison["real_ann_return_delta_pp"].gt(1e-10)
        | comparison["real_max_dd_delta_pp"].gt(1e-10)
    )
    comparison["pass_both_segments"] = comparison["full_pass_either"] & comparison["real_pass_either"]
    comparison["_sort_off"] = comparison["candidate"].ne("r2_off").astype(int)
    comparison = comparison.sort_values(["_sort_off", "r2_window", "r2_threshold"], na_position="first").drop(columns="_sort_off")
    output_csv = OUT / "ic_r2_full_real_all_candidates_comparison.csv"
    comparison.to_csv(output_csv, index=False, encoding="utf-8-sig")
    figure = _plot(comparison, baseline)
    report = f"""# IC R2：全样本与真实期逐候选对照

- 基线为 `r2_off`：全样本 {baseline['full_start']}—{baseline['full_end']}，年化 {baseline['full_ann_return']:.2%}，最大回撤 {baseline['full_max_drawdown']:.2%}；真实期 {baseline['real_start']}—{baseline['real_end']}，年化 {baseline['real_ann_return']:.2%}，最大回撤 {baseline['real_max_drawdown']:.2%}。
- CSV 含 `r2_off` 加 231 个 R2 window × threshold 候选；每行都同时给出绝对指标和相对基线的百分点变化。最大回撤 Δ 为正表示回撤变浅。
- 热图颜色是相对 `r2_off` 的百分点变化；绿色为改善。R2 仅改变 0.5 倍动量 F 腿，固定 F、成本、现金、换约保持匹配，网格/Put/Call 未纳入本表。

文件：`ic_r2_full_real_all_candidates_comparison.csv`、`ic_r2_full_real_all_candidates_heatmap.png`。
"""
    (OUT / "ic_r2_full_real_comparison.md").write_text(report, encoding="utf-8")
    print({"rows": len(comparison), "csv": str(output_csv), "figure": str(figure)})


if __name__ == "__main__":
    main()
