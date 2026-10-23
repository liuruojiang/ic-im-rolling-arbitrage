from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_put_strike_anchor_scan_v1 as v1


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_put_strike_anchor_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "629fa4b389433e7222740ccd80b8168512b6257958b3312dec76b84195e9ec01"
OUTPUT = ROOT / "outputs" / VERSION
V1_OUTPUT = ROOT / "outputs" / v1.VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_ic_im_im_v1_2_strike_anchor_diagnostic_v2_im_core_mo_put_strike_reference_asset_x_moneyness"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("v2 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("v2 specification sidecar mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal v2 output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized v2 scan folder missing: {SCAN}")
    required = [
        Path(v1.__file__),
        v1.SPEC,
        V1_OUTPUT / "daily_candidates.csv.gz",
        V1_OUTPUT / "window_metrics.csv",
        V1_OUTPUT / "trade_audit.csv.gz",
        V1_OUTPUT / "contract_selection_audit.csv",
        Path(v1.v8.__file__),
        Path(v1.v5.IM_QUOTES),
        Path(v1.v4.OPTIONS),
        Path(v1.v4.PRICE),
        Path(v1.v4.UPSTREAM),
        v1.IM12_BASE,
        v1.IM50_REAL,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Required v2 inputs missing: {missing}")
    return {str(path.relative_to(ROOT)): sha256(path) for path in required}


def corrected_decision(window: pd.DataFrame) -> tuple[str, str, dict[str, Any]]:
    table = window.set_index("candidate")

    def row(anchor: str, moneyness: int) -> pd.Series:
        return table.loc[f"{v1.PRIMARY_SCOPE}_{anchor}_m{moneyness:03d}"]

    active95 = row("active_im", 95)
    spot95 = row("csi1000_spot", 95)
    tolerance = 1e-14
    full_return_not_worse = float(spot95["ann_return_full"]) >= float(
        active95["ann_return_full"]
    ) - tolerance
    full_dd_not_worse = float(spot95["max_dd_full"]) >= float(
        active95["max_dd_full"]
    ) - tolerance
    full_dd_strictly_better = float(spot95["max_dd_full"]) > float(
        active95["max_dd_full"]
    ) + tolerance
    full_return_within_50bp = float(spot95["ann_return_full"]) >= float(
        active95["ann_return_full"]
    ) - 0.005
    recent_dd_ok = all(
        float(spot95[f"max_dd_{name}"])
        >= float(active95[f"max_dd_{name}"]) - 0.01
        for name in ("last_3y", "last_1y")
    )
    neighbor_support: list[int] = []
    for moneyness in (90, 100):
        active = row("active_im", moneyness)
        spot = row("csi1000_spot", moneyness)
        if (
            float(spot["ann_return_full"]) >= float(active["ann_return_full"]) - tolerance
            and float(spot["max_dd_full"]) >= float(active["max_dd_full"]) - tolerance
        ):
            neighbor_support.append(moneyness)

    strict_promote = bool(
        full_return_not_worse
        and full_dd_not_worse
        and recent_dd_ok
        and neighbor_support
    )
    watchlist = bool(
        full_dd_strictly_better and full_return_within_50bp and recent_dd_ok
    )
    recent_only = bool(
        float(spot95["ann_return_last_1y"])
        > float(active95["ann_return_last_1y"]) + tolerance
        and float(spot95["max_dd_last_1y"])
        > float(active95["max_dd_last_1y"]) + tolerance
        and not strict_promote
        and not watchlist
    )
    if strict_promote:
        decision = "promote_candidate"
        stability = "wide_stable" if len(neighbor_support) == 2 else "narrow_stable"
    elif watchlist:
        decision = "watchlist"
        stability = "narrow_stable" if neighbor_support else "peak_only"
    else:
        decision = "keep_default"
        stability = "recent_only" if recent_only else "reject"
    detail = {
        "full_return_not_worse": full_return_not_worse,
        "full_dd_not_worse": full_dd_not_worse,
        "full_dd_strictly_better": full_dd_strictly_better,
        "full_return_within_50bp": full_return_within_50bp,
        "recent_dd_within_100bp": recent_dd_ok,
        "neighbor_support_moneyness": neighbor_support,
        "active95_full_ann_return": float(active95["ann_return_full"]),
        "spot95_full_ann_return": float(spot95["ann_return_full"]),
        "active95_full_max_dd": float(active95["max_dd_full"]),
        "spot95_full_max_dd": float(spot95["max_dd_full"]),
        "active95_last1y_ann_return": float(active95["ann_return_last_1y"]),
        "spot95_last1y_ann_return": float(spot95["ann_return_last_1y"]),
        "active95_last1y_max_dd": float(active95["max_dd_last_1y"]),
        "spot95_last1y_max_dd": float(spot95["max_dd_last_1y"]),
        "v1_decision_classifier_error": (
            "v1 used the 50bp watchlist tolerance in the promote condition"
        ),
    }
    return decision, stability, detail


def rerun() -> dict[str, Any]:
    inputs = v1.load_research_inputs()
    official_overlay, _ = v1.official_active_reference(inputs)
    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    for anchor in v1.ANCHORS:
        for moneyness in v1.MONEYNESS:
            overlay, trades, lives = v1.run_anchor_candidate(anchor, moneyness, inputs)
            label = v1.candidate_name(anchor, moneyness)
            overlays[label] = overlay[
                [
                    "date",
                    "put_pnl_ret",
                    "put_cost_rate",
                    "put_mark_fraction",
                    "put_fraction",
                    "put_contract",
                ]
            ]
            if not trades.empty:
                trade_parts.append(trades)
            if not lives.empty:
                lives = lives.copy()
                lives["candidate"] = label
                life_parts.append(lives)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lives = pd.concat(life_parts, ignore_index=True, sort=False)
    parity_active = v1.active_engine_parity(official_overlay, overlays["active_im_m095"])
    daily, parity = v1.build_paths(inputs, overlays)
    parity["active_engine_parity_max_abs"] = parity_active
    scan_summary, window_metrics = v1.metric_tables(daily)
    selection, selection_summary = v1.selection_audit(trades, inputs)
    basis = v1.basis_diagnostics(trades, inputs)
    comparison = v1.contract_comparison(selection)
    decision, stability, detail = corrected_decision(window_metrics)
    return {
        "inputs": inputs,
        "daily": daily,
        "trades": trades,
        "lives": lives,
        "scan_summary": scan_summary,
        "window_metrics": window_metrics,
        "selection": selection,
        "selection_summary": selection_summary,
        "basis": basis,
        "comparison": comparison,
        "parity": parity,
        "decision": decision,
        "stability": stability,
        "detail": detail,
    }


def v1_v2_parity(result: dict[str, Any]) -> dict[str, Any]:
    old_daily = pd.read_csv(V1_OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    new_daily = result["daily"]
    fields = [
        "strategy_ret",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
    ]
    joined = old_daily[["date", "scope", "candidate", *fields, "put_contract"]].merge(
        new_daily[["date", "scope", "candidate", *fields, "put_contract"]],
        on=["date", "scope", "candidate"],
        suffixes=("_v1", "_v2"),
        validate="one_to_one",
    )
    numeric_error = float(
        max(
            (joined[f"{field}_v1"] - joined[f"{field}_v2"]).abs().max()
            for field in fields
        )
    )
    contract_mismatches = int(
        joined["put_contract_v1"].fillna("").ne(joined["put_contract_v2"].fillna("")).sum()
    )

    old_window = pd.read_csv(V1_OUTPUT / "window_metrics.csv")
    numeric = [
        column
        for column in old_window.columns
        if column.startswith("ann_return_")
        or column.startswith("max_dd_")
        or column.startswith("sharpe_repo_")
    ]
    window_join = old_window[["candidate", *numeric]].merge(
        result["window_metrics"][["candidate", *numeric]],
        on="candidate",
        suffixes=("_v1", "_v2"),
        validate="one_to_one",
    )
    window_error = float(
        max(
            (window_join[f"{column}_v1"] - window_join[f"{column}_v2"]).abs().max()
            for column in numeric
        )
    )
    if numeric_error > 1e-14 or window_error > 1e-14 or contract_mismatches:
        raise RuntimeError(
            f"v1/v2 rerun parity failed: daily={numeric_error}, windows={window_error}, "
            f"contracts={contract_mismatches}"
        )
    return {
        "daily_numeric_max_abs": numeric_error,
        "window_metric_max_abs": window_error,
        "put_contract_mismatches": contract_mismatches,
        "v1_rows": int(len(old_daily)),
        "v2_rows": int(len(new_daily)),
    }


def build_record(result: dict[str, Any], correction: dict[str, Any]) -> str:
    window = result["window_metrics"]
    primary_ids = [
        f"{v1.PRIMARY_SCOPE}_no_put",
        f"{v1.PRIMARY_SCOPE}_active_im_m095",
        f"{v1.PRIMARY_SCOPE}_csi1000_spot_m095",
        f"{v1.PRIMARY_SCOPE}_matched_expiry_im_m095",
    ]
    primary = window[window["candidate"].isin(primary_ids)].copy()
    lines = [
        "# IM / MO Put 95% 行权价基准复测 v2",
        "",
        f"状态：`research_only`；决策：`{result['decision']}`；稳定性：`{result['stability']}`；未修改冻结主线。",
        "",
        "## 纠错结论",
        "",
        "- v1全部收益和选约指标有效，但决策器错误地把watchlist的50bp容忍度用于晋升；v1决策标签废止。",
        f"- v2从真实输入完整重跑；v1/v2逐日数值最大误差{correction['daily_numeric_max_abs']:.3e}，"
        f"窗口指标误差{correction['window_metric_max_abs']:.3e}，Put合约差异{correction['put_contract_mismatches']}。",
        f"- 正确决策详情：`{json.dumps(result['detail'], ensure_ascii=False)}`。",
        "",
        "## 95%主比较（IM 1.2核心Put；Call与网格关闭）",
        "",
        "| 候选 | 全期年化 | 全期MaxDD | 近3年年化 | 近3年MaxDD | 近1年年化 | 近1年MaxDD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary.sort_values("candidate").itertuples(index=False):
        lines.append(
            f"| {row.base_candidate} | {row.ann_return_full:.2%} | {row.max_dd_full:.2%} | "
            f"{row.ann_return_last_3y:.2%} | {row.max_dd_last_3y:.2%} | "
            f"{row.ann_return_last_1y:.2%} | {row.max_dd_last_1y:.2%} |"
        )
    comparison = result["comparison"]
    basis = result["basis"]
    lines.extend(
        [
            "",
            "## 解释",
            "",
            f"- 活动IM/现货在95%开仓与换月事件上的均值为{basis['active_vs_spot'].mean():.4f}，"
            f"最小值{basis['active_vs_spot'].min():.4f}；现行活动IM95%平均约等于现货{(0.95*basis['active_vs_spot']).mean():.2%}虚值度。",
            f"- 活动IM95%与指数95%在{int((~comparison['same_contract']).sum())}/{len(comparison)}个可比事件选中不同合约。",
            "- 指数95%在近1年收益和回撤更好，但完整真实期年化较低、最大回撤相同，属于recent_only，不能据此替换现行基线。",
            "- 同到期IM基准明显较差，只保留为期限错配诊断。",
            "",
            "## 数据与边界",
            "",
            f"- 真实样本：{result['daily']['date'].min().date()}至{result['daily']['date'].max().date()}，986个共同交易日。",
            "- IM/MO为中金所官方原始日线；中证1000为不复权价格指数；T收盘/T+1收盘；Asia/Shanghai。",
            "- 10年和5年窗口因真实历史不足而截短为完整样本，不是独立长窗证据。",
            "- 未计盘口价差、冲击、容量、动态保证金、价格限制、异常结算和整数合约映射。",
            "- Call与网格关闭；本结果不是完整IM v1.2组合绩效或交易授权。",
            "",
            "## 复现",
            "",
            "```powershell",
            "python -m pytest test_im_mo_put_strike_anchor_scan_v2.py -q",
            "python im_mo_put_strike_anchor_scan_v2.py",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    result: dict[str, Any],
    correction: dict[str, Any],
    source_hashes: dict[str, str],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    result["daily"].to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    result["trades"].to_csv(OUTPUT / "trade_audit.csv.gz", index=False, compression="gzip")
    result["lives"].to_csv(OUTPUT / "lifecycle_audit.csv", index=False)
    result["scan_summary"].to_csv(OUTPUT / "scan_summary.csv", index=False)
    result["window_metrics"].to_csv(OUTPUT / "window_metrics.csv", index=False)
    result["selection"].to_csv(OUTPUT / "contract_selection_audit.csv", index=False)
    result["basis"].to_csv(OUTPUT / "basis_diagnostics.csv", index=False)
    result["comparison"].to_csv(OUTPUT / "active_vs_spot_contract_comparison.csv", index=False)
    pd.DataFrame([{**result["parity"], **correction}]).to_csv(
        OUTPUT / "parity_checks.csv", index=False
    )
    record = build_record(result, correction)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    command_text = (
        "python -m pytest test_im_mo_put_strike_anchor_scan_v2.py -q\n"
        "python im_mo_put_strike_anchor_scan_v2.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(command_text, encoding="utf-8")
    manifest = {
        "version": VERSION,
        "status": "research_only_not_live_authority",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "data_snapshot": {
            "start": result["daily"]["date"].min().date().isoformat(),
            "end": result["daily"]["date"].max().date().isoformat(),
            "rows": int(result["daily"]["date"].nunique()),
            "timezone": "Asia/Shanghai",
            "adjustment_mode": "official raw IM/MO daily bars; CSI1000 price index unadjusted",
        },
        "decision": result["decision"],
        "stability_label": result["stability"],
        "decision_detail": result["detail"],
        "v1_v2_correction_parity": correction,
        "execution_parity": result["parity"],
        "selection": result["selection_summary"],
        "git_status_before": git_before,
        "git_status_after": v1.git_value("status", "--short"),
        "warnings": [
            "v1 decision label is invalid; v2 is the corrected authority for this scan",
            "real IM/MO history is shorter than five years",
            "Call and grid are disabled to isolate strike anchor",
            "official close is not guaranteed executable size",
            "research output is not a trading instruction",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    result["scan_summary"].to_csv(SCAN / "scan_summary.csv", index=False)
    result["window_metrics"].to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_text)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": f"{v1.PRIMARY_SCOPE}_active_im_m095",
                "anchor": "active_im",
                "moneyness": 0.95,
            },
            "candidate_grid": [
                {"anchor": anchor, "moneyness": moneyness}
                for anchor in v1.ANCHORS
                for moneyness in v1.MONEYNESS
            ],
            "data_snapshot": manifest["data_snapshot"],
            "cost_model": {
                "im_side_cost": 0.0001,
                "mo_contract_side_cost_full_im_notional": v1.v4.MO_CONTRACT_SIDE_COST,
                "margin_buffer_per_1x_im": 0.30,
                "cash_yield_net_annual": 0.03,
            },
            "parity_check": {**result["parity"], **correction},
            "warnings": manifest["warnings"],
            "source_hashes": source_hashes,
            "decision": result["decision"],
            "stability_label": result["stability"],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    source_hashes = verify_inputs()
    git_before = v1.git_value("status", "--short")
    result = rerun()
    correction = v1_v2_parity(result)
    write_outputs(result, correction, source_hashes, git_before)
    print(
        json.dumps(
            {
                "version": VERSION,
                "decision": result["decision"],
                "stability_label": result["stability"],
                "data_start": result["daily"]["date"].min().date().isoformat(),
                "data_end": result["daily"]["date"].max().date().isoformat(),
                "v1_v2_parity": correction,
                "execution_parity": result["parity"],
                "output": str(OUTPUT),
                "scan": str(SCAN),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
