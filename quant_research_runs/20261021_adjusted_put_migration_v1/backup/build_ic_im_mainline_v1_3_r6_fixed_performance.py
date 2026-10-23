"""Build immutable v1.3-r6 curves from frozen IC and exact IM replay."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_im_mainline_v1_3 as target_module


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_mainline_v1_3_r6_fixed_performance"
STATUS = "research_only_fixed_reference_not_live_authority"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SPEC = ROOT / "docs" / "ic_im_mainline_v1_3_r6_spec.md"
IC_SOURCE = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "ic_daily.csv.gz"
IC_METRICS_SOURCE = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "metrics_by_window.csv"
IM_REPLAY = ROOT / "outputs" / "im_v13_momentum_put_independent_replay_v1"
IM_CURVES_SOURCE = IM_REPLAY / "daily_curves.csv.gz"
IM_METRICS_SOURCE = IM_REPLAY / "metrics_by_window.csv"
IM_LEDGERS_SOURCE = IM_REPLAY / "put_daily_ledgers.csv.gz"
IM_TRADES_SOURCE = IM_REPLAY / "put_trades.csv"
IM_LIFECYCLES_SOURCE = IM_REPLAY / "put_lifecycles.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _metric(frame: pd.DataFrame, start: pd.Timestamp, *, include_initial: bool) -> dict[str, object]:
    sample = frame.loc[frame["date"].ge(start)].copy()
    if not include_initial:
        sample = sample.iloc[1:].copy()
    if sample.empty:
        raise RuntimeError(f"Empty metric sample from {start.date()}")
    ret = sample["ret"].astype(float)
    if not np.isfinite(ret.to_numpy()).all() or (ret <= -1.0).any():
        raise RuntimeError("Return series contains nonfinite values or total loss days")
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    return {
        "available": True,
        "reason": "",
        "rows": int(len(sample)),
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0),
        "ann_vol": std * np.sqrt(252.0),
        "sharpe": float(ret.mean()) / std * np.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(dd.min()),
        "final_nav": float(nav.iloc[-1]),
    }


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "reason": reason,
        "rows": np.nan,
        "start": "",
        "end": "",
        "ann_return": np.nan,
        "ann_vol": np.nan,
        "sharpe": np.nan,
        "max_dd": np.nan,
        "final_nav": np.nan,
    }


def build() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    ic = pd.read_csv(IC_SOURCE, parse_dates=["date"])
    curves = pd.read_csv(IM_CURVES_SOURCE, parse_dates=["date"])
    im = curves.loc[curves["strategy"].eq("core_plus_momentum_independent_replay")].copy()
    base = curves.loc[curves["strategy"].eq("core_only_independent_replay")].copy()
    if im.empty or base.empty or len(im) != len(base):
        raise RuntimeError("Independent IM replay does not contain aligned current/candidate curves")
    if not im["date"].reset_index(drop=True).equals(base["date"].reset_index(drop=True)):
        raise RuntimeError("Independent IM replay strategies do not share a date index")

    ledgers = pd.read_csv(IM_LEDGERS_SOURCE, parse_dates=["date"])
    pivot = ledgers.pivot(index="date", columns="sleeve", values="put_qty_normalized").reset_index()
    joined = im[["date", "momentum_weight"]].merge(
        pivot[["date", "core", "momentum"]],
        on="date",
        how="inner",
        validate="one_to_one",
    )
    momentum_target_error = float(
        (joined["core"] * joined["momentum_weight"] - joined["momentum"]).abs().max()
    )
    if len(joined) != len(im) or momentum_target_error > 1e-12:
        raise RuntimeError(
            f"v1.3-r6 target and independent momentum ledger mismatch: rows={len(joined)}, error={momentum_target_error}"
        )

    end = pd.Timestamp("2026-08-14")
    windows = {
        "full": pd.Timestamp("2015-04-16"),
        "10y": end - pd.DateOffset(years=10),
        "5y": end - pd.DateOffset(years=5),
        "3y": end - pd.DateOffset(years=3),
        "1y": end - pd.DateOffset(years=1),
    }
    rows: list[dict[str, object]] = []
    for window, start in windows.items():
        rows.append(
            {
                "product": "IC",
                "window": window,
                **_metric(ic, start, include_initial=(window == "full")),
            }
        )
    im_starts = {
        "real_full": pd.Timestamp("2022-07-22"),
        "3y": end - pd.DateOffset(years=3),
        "1y": end - pd.DateOffset(years=1),
    }
    rows.extend(
        {
            "product": "IM",
            "window": window,
            **_metric(im, start, include_initial=False),
        }
        for window, start in im_starts.items()
    )
    unavailable_reason = "real_IM_MO_history_shorter_than_requested_window"
    rows.extend(
        {"product": "IM", "window": window, **_unavailable(unavailable_reason)}
        for window in ("5y", "10y")
    )
    table = pd.DataFrame(rows)

    prior_ic = pd.read_csv(IC_METRICS_SOURCE)
    ic_check = table.loc[table["product"].eq("IC")].merge(
        prior_ic.loc[prior_ic["product"].eq("IC")], on=["product", "window"], suffixes=("_new", "_old")
    )
    metric_columns = ["ann_return", "ann_vol", "sharpe", "max_dd", "final_nav"]
    ic_metric_error = max(
        float((ic_check[f"{column}_new"] - ic_check[f"{column}_old"]).abs().max())
        for column in metric_columns
    )
    if ic_metric_error > 1e-12:
        raise RuntimeError(f"IC v1.3 metric parity failed: {ic_metric_error}")

    frozen_im_metrics = pd.read_csv(IM_METRICS_SOURCE)
    candidate_frozen = frozen_im_metrics.loc[
        frozen_im_metrics["strategy"].eq("core_plus_momentum_independent_replay")
        & frozen_im_metrics["available"].astype(bool)
    ].copy()
    window_map = {"full": "real_full", "last_3y": "3y", "last_1y": "1y"}
    errors = []
    for frozen_window, output_window in window_map.items():
        frozen = candidate_frozen.loc[candidate_frozen["window"].eq(frozen_window)].iloc[0]
        current = table.loc[
            table["product"].eq("IM") & table["window"].eq(output_window)
        ].iloc[0]
        errors.extend(abs(float(current[column]) - float(frozen[column])) for column in metric_columns[:-1])
    im_metric_error = float(max(errors))
    if im_metric_error > 1e-12:
        raise RuntimeError(f"IM independent replay metric parity failed: {im_metric_error}")

    current_metric = _metric(base, pd.Timestamp("2022-07-22"), include_initial=False)
    candidate_metric = _metric(im, pd.Timestamp("2022-07-22"), include_initial=False)
    comparison = pd.DataFrame(
        [
            {"strategy": "v1.3_core_put_only", **current_metric},
            {"strategy": "v1.3_r6_core_plus_momentum_put", **candidate_metric},
        ]
    )
    validation = {
        "version": VERSION,
        "status": STATUS,
        "IC_daily_source_sha256": sha256(IC_SOURCE),
        "IC_metrics_parity_max_abs_error": ic_metric_error,
        "IM_replay_daily_source_sha256": sha256(IM_CURVES_SOURCE),
        "IM_replay_metrics_parity_max_abs_error": im_metric_error,
        "IM_momentum_target_vs_ledger_max_abs_error": momentum_target_error,
        "IM_real_rows": int(len(im)),
        "IM_min_cash_weight": float(im["cash_weight"].min()),
        "IM_all_returns_finite": bool(np.isfinite(im["ret"].astype(float).to_numpy()).all()),
        "IM_date_unique_increasing": bool(im["date"].is_unique and im["date"].is_monotonic_increasing),
        "v1_3_r5_to_r6_real_full": {
            "ann_return_delta": float(candidate_metric["ann_return"] - current_metric["ann_return"]),
            "sharpe_delta": float(candidate_metric["sharpe"] - current_metric["sharpe"]),
            "max_dd_improvement": float(candidate_metric["max_dd"] - current_metric["max_dd"]),
        },
        "orders_generated": False,
        "live_ledger_migrated": False,
    }
    return ic, im, table, comparison, validation


def main() -> None:
    inputs = [
        SPEC,
        IC_SOURCE,
        IC_METRICS_SOURCE,
        IM_CURVES_SOURCE,
        IM_METRICS_SOURCE,
        IM_LEDGERS_SOURCE,
        IM_TRADES_SOURCE,
        IM_LIFECYCLES_SOURCE,
        ROOT / "im_mainline_v1_3.py",
        ROOT / "ic_im_mainline_v1_3.py",
    ]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing v1.3-r6 fixed-performance inputs: {missing}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Immutable output or staging path exists: {OUTPUT} / {STAGING}")
    STAGING.mkdir(parents=True)
    try:
        ic, im, table, comparison, validation = build()
        ic.to_csv(STAGING / "ic_daily.csv.gz", index=False, compression="gzip")
        im.to_csv(STAGING / "im_daily_real.csv.gz", index=False, compression="gzip")
        table.to_csv(STAGING / "metrics_by_window.csv", index=False)
        comparison.to_csv(STAGING / "im_v1_3_r5_vs_r6_real_full.csv", index=False)
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        im_old = comparison.iloc[0]
        im_new = comparison.iloc[1]
        lines = [
            "# IC/IM 动量 Put v1.3-r6 定值绩效",
            "",
            f"状态：`{STATUS}`；不生成订单；r5账本只通过独立迁移器进入r6。",
            "",
            "## 结论",
            "",
            "- IC规则和逐日收益完全沿用v1.3，没有变化。",
            f"- IM真实MO区间：r5 CAGR {im_old.ann_return:.2%}、Sharpe {im_old.sharpe:.3f}、MaxDD {im_old.max_dd:.2%}；r6分别为 {im_new.ann_return:.2%}、{im_new.sharpe:.3f}、{im_new.max_dd:.2%}。",
            f"- IM变化：CAGR {float(im_new.ann_return-im_old.ann_return):+.2%}，Sharpe {float(im_new.sharpe-im_old.sharpe):+.3f}，最大回撤改善 {float(im_new.max_dd-im_old.max_dd):+.2%}。",
            "- IM 5Y/10Y为N/A：真实MO历史不足，不用理论Put补齐。",
            "",
            "## 规则边界",
            "",
            "- IC动量Put保持v1.3-r5估值-only规则；IM动量Put使用完整current_4tier_mom3目标并采用独立合约账本。",
            "- IC/IM网格仓仍无Put；IC无Call；IM Call仍只覆盖核心仓。",
            "- 结果已计既有期货/期权成本与3%现金收益，未计价差、冲击、容量、涨跌停、动态保证金和实际账户整数映射。",
        ]
        (STAGING / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest = {
            "version": VERSION,
            "status": STATUS,
            "created_at": datetime.now().astimezone().isoformat(),
            "target_rules": target_module.rule_manifest(),
            "inputs": {str(path.relative_to(ROOT)): sha256(path) for path in inputs},
            "outputs": [
                "ic_daily.csv.gz",
                "im_daily_real.csv.gz",
                "metrics_by_window.csv",
                "im_v1_3_r5_vs_r6_real_full.csv",
                "validation.json",
                "record.md",
            ],
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
    except Exception:
        # Keep staging for diagnosis; never overwrite or silently discard formal evidence.
        raise
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
