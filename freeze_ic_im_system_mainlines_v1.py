from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_system_mainlines_v1"
SPEC = ROOT / "docs/ic_im_system_mainlines_v1_spec.md"
SPEC_SIDECAR = ROOT / "docs/ic_im_system_mainlines_v1_spec.md.sha256"
SPEC_HASH = "a7ed851fdae45a16cd14d05449730221a8a2c804fb2be56efaedafb7ab0eb2fc"
OUTPUT = ROOT / f"outputs/{VERSION}"
STAGING = ROOT / f"outputs/.{VERSION}.staging"

IC_DAILY = ROOT / "outputs/ic_put_grid_call_combined_v2/daily_candidates.csv.gz"
IC_METRICS = ROOT / "outputs/ic_put_grid_call_combined_v2/metrics_by_window.csv"
IC_INTEGRITY = ROOT / "outputs/ic_put_grid_call_combined_v2/integrity_checks.json"
IC_MANIFEST = ROOT / "outputs/ic_put_grid_call_combined_v2/output_manifest.json"
IM_DAILY = ROOT / "outputs/im_put_grid_call_final_audit_v1/daily_candidates.csv.gz"
IM_METRICS = ROOT / "outputs/im_put_grid_call_final_audit_v1/metrics_by_window.csv"
IM_BREACH = ROOT / "outputs/im_put_grid_call_final_audit_v1/capital_breach_details.csv"
IM_INTEGRITY = ROOT / "outputs/im_put_grid_call_final_audit_v1/integrity_checks.json"
IM_MANIFEST = ROOT / "outputs/im_put_grid_call_final_audit_v1/output_manifest.json"
DATE_MANIFEST = ROOT / "outputs/option_expiry_semantics_audit_v1/output_manifest.json"

FROZEN_HASHES = {
    IC_DAILY: "15e38d5754f25bddf829b5fec1b8692c1d6a55a4af902385740f5f507ead15b2",
    IC_METRICS: "d51a6ae618e0f9935bd2af2bd4b2df56ea3a191b5cee6dca45946dcddc65c26c",
    IC_INTEGRITY: "23f6e22225a6722322636feeb1eaea6e9821407812b09d0b79288008638391a1",
    IC_MANIFEST: "caa5d75a01e47688856adbb62f38636af0490a2166fc61c9034ba13bc445b636",
    IM_DAILY: "21fa70cf2ca9df2e5a9b9c9ed7b255cce8a7b430fc9a62725706a71d9422837a",
    IM_METRICS: "d904a8a579a56fa02f58536c380b1a8c8f74620403e6295c8a135cd9eae10cf6",
    IM_BREACH: "fd52a7fefc6d64f715918643d9524bc255ec82a7c87df6d78023700329a2a275",
    IM_INTEGRITY: "096d7e0284d5e77d074ce2df97bac935e4537d0221f36b30358bcfc78bfeffda",
    IM_MANIFEST: "088d7068410bf8a24a32f9581a7ed778e881279cd74f68afe63eb2ac38b24f4c",
    DATE_MANIFEST: "8ce51371e0b710fe31c1ffa1bd30bb3aeba02423fd89ade6b68407a209c0d92e",
    ROOT / "docs/ic_510500_put_research_mainline_v1.md": "6da92d886f184277cffcdbbbd706d43ee057c7e1d4502410b8c7b12cde8eb4b5",
    ROOT / "docs/ic_valuation_overlay_grid_research_mainline_v1.md": "c1fab691fa84bca1a760f84f1fb63f12a7db1ff485cc4e119c0da8986d940487",
    ROOT / "docs/im_mo_put_research_mainline_v1.md": "0caafc8a48518babd68108e067d3b61e4cda4694b7ac2b3c90dfda8718330738",
    ROOT / "docs/im_mo_call_daily_d10_threat5_research_mainline_v1.md": "815f82c0a21c1795632daa1e58cfea33844c31329d3b91c9ed5b892a0d9af78d",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(directory: Path) -> bool:
    data = json.loads((directory / "output_manifest.json").read_text(encoding="utf-8"))
    entries = data.get("files", data)
    return all(
        (directory / name).exists()
        and sha256(directory / name) == metadata["sha256"]
        for name, metadata in entries.items()
    )


def main() -> None:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal mainline freeze output already exists")
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen mainline specification hash mismatch")
    if SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower() != SPEC_HASH:
        raise RuntimeError("Frozen mainline specification sidecar mismatch")
    mismatches = {
        str(path.relative_to(ROOT)): {"expected": expected, "actual": sha256(path) if path.exists() else "missing"}
        for path, expected in FROZEN_HASHES.items()
        if not path.exists() or sha256(path) != expected
    }
    if mismatches:
        raise RuntimeError(f"Frozen source mismatch: {mismatches}")
    manifest_pass = {
        "ic_combined_v2": verify_manifest(IC_DAILY.parent),
        "im_final_audit_v1": verify_manifest(IM_DAILY.parent),
        "option_date_audit_v1": verify_manifest(DATE_MANIFEST.parent),
    }
    if not all(manifest_pass.values()):
        raise RuntimeError(f"Upstream output manifest failure: {manifest_pass}")

    ic = pd.read_csv(IC_DAILY)
    ic = ic[ic["candidate"].isin(["model_grid_only", "real_grid_only"])].copy()
    ic_call_columns = [
        "call_pnl_ret",
        "call_cost_rate",
        "call_mark_fraction",
        "call_margin_fraction",
        "call_coverage",
    ]
    ic_call_abs_max = float(ic[ic_call_columns].fillna(0.0).abs().to_numpy().max())
    ic_has_call_rows = int(ic["has_call"].fillna(False).astype(bool).sum())
    ic_contract_rows = int(ic["call_contract"].fillna("").astype(str).ne("").sum())
    ic_units = sorted(float(value) for value in ic["total_ic_units"].dropna().unique())

    im = pd.read_csv(IM_DAILY)
    im = im[im["candidate"].eq("full_put_grid_call")].copy()
    im_integrity = json.loads(IM_INTEGRITY.read_text(encoding="utf-8"))
    breach = pd.read_csv(IM_BREACH)
    breach["operational_margin_rate"] = 0.15
    breach["operational_morning_capital"] = (
        breach["morning_capital_proxy"] - 0.15 * breach["total_im_units"]
    )
    breach["operational_breach"] = breach["operational_morning_capital"] > 1.0 + 1e-12
    im_eod_capital_15 = (
        0.15 * im["total_im_units"]
        + im["put_mark_fraction"].fillna(0.0)
        + im["call_margin_fraction"].fillna(0.0)
    )

    checks = {
        "upstream_manifests_pass": all(manifest_pass.values()),
        "ic_mainline_rows_present": len(ic) > 0,
        "ic_call_economic_fields_zero": ic_call_abs_max == 0.0,
        "ic_has_call_false": ic_has_call_rows == 0,
        "ic_call_contract_empty": ic_contract_rows == 0,
        "ic_units_are_1_or_2": set(ic_units).issubset({1.0, 2.0}),
        "ic_upstream_integrity_pass": bool(json.loads(IC_INTEGRITY.read_text(encoding="utf-8"))["all_checks_passed"]),
        "im_mainline_rows_present": len(im) > 0,
        "im_put_not_scaled_with_grid": im_integrity["put_scaled_with_grid_errors"] == 0,
        "im_call_not_scaled_with_grid": im_integrity["call_scaled_with_grid_errors"] == 0,
        "im_components_isolated": im_integrity["full_component_isolation_max_abs"] <= 1e-12,
        "im_event_and_call_rules_pass": im_integrity["event_causality_errors"] == 0 and im_integrity["call_rule_errors"] == 0,
        "im_operational_15pct_old_breaches_resolved": not bool(breach["operational_breach"].any()),
        "im_eod_operational_15pct_below_100": float(im_eod_capital_15.max()) <= 1.0 + 1e-12,
        "research_only_not_live_approved": True,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Mainline freeze checks failed: {checks}")

    ic_metrics = pd.read_csv(IC_METRICS)
    ic_metrics = ic_metrics[ic_metrics["candidate"].isin(["model_grid_only", "real_grid_only"])].copy()
    ic_metrics.insert(0, "system", "IC")
    im_metrics = pd.read_csv(IM_METRICS)
    im_metrics = im_metrics[im_metrics["candidate"].eq("full_put_grid_call")].copy()
    im_metrics.insert(0, "system", "IM")
    keep_columns = [
        "system",
        "layer",
        "candidate",
        "window",
        "available",
        "actual_start",
        "end",
        "rows",
        "total_return",
        "ann_return",
        "ann_vol",
        "sharpe_repo",
        "max_dd",
    ]
    metrics = pd.concat([ic_metrics[keep_columns], im_metrics[keep_columns]], ignore_index=True)

    STAGING.mkdir(parents=True)
    metrics.to_csv(STAGING / "mainline_metrics.csv", index=False, encoding="utf-8-sig")
    breach.to_csv(STAGING / "im_operational_capital_15pct.csv", index=False, encoding="utf-8-sig")
    state = {
        "version": VERSION,
        "ic": {
            "components": ["roll_ic_1x", "buy_put_v21", "grid_0.375_1.000_add_1x_no_put"],
            "call": "excluded",
            "source_candidates": ["model_grid_only", "real_grid_only"],
        },
        "im": {
            "components": ["roll_im_1x", "buy_put_floor3", "grid_0.85_1.25_add_1x", "sell_call_d10_iv26_threat5"],
            "call_scope": "fixed_core_only",
            "rescue_expiry": "strictly_later_nearest_listed_expiry_not_calendar_plus_1m",
            "source_candidate": "full_put_grid_call",
        },
        "performance_margin_buffer": 0.30,
        "operational_margin_user_upper_bound": 0.15,
        "operational_margin_independently_verified": False,
        "live_approved": False,
    }
    (STAGING / "mainline_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "integrity_checks.json").write_text(json.dumps({"checks": checks, "manifest_pass": manifest_pass}, ensure_ascii=False, indent=2), encoding="utf-8")
    source_manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "frozen_sources": {str(path.relative_to(ROOT)): value for path, value in FROZEN_HASHES.items()},
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    ic_real_full = metrics[(metrics["system"] == "IC") & (metrics["layer"] == "real") & (metrics["window"] == "full")].iloc[0]
    im_real_full = metrics[(metrics["system"] == "IM") & (metrics["layer"] == "real") & (metrics["window"] == "full")].iloc[0]
    record = f"""# 滚 IC / IM 系统研究主线冻结 v1

状态：`mainlines_frozen_research_only`；未批准实盘。

## 最终主线

- IC：1倍滚IC + 1.90/2.00/2.10三级买Put、MOM120最低25% + 0.375/1.000新增1倍网格；网格仓不加Put；**不卖Call**。
- IM：1倍滚IM + 重建估值/MOM120最低3张Put + 0.85/1.25新增1倍网格 + 每日D10/IV26/5%救援Call；Put和Call只按冻结规则覆盖固定底仓，网格仓不增加保护或Call覆盖。
- IM救援期限固定为`rescue_next_listed`：相对旧到期日严格更晚的最近实际挂牌到期日，不是固定增加一个日历月。

## 冻结历史结果

- IC真实主线路径全样本CAGR/MaxDD：{float(ic_real_full['ann_return']):.2%} / {float(ic_real_full['max_dd']):.2%}。
- IM真实主线路径全样本CAGR/MaxDD：{float(im_real_full['ann_return']):.2%} / {float(im_real_full['max_dd']):.2%}。
- 上述数字直接提取既有首次正式输出；本版没有重新计算或优化收益。

## 资金复核

绩效现金收益仍沿用每1倍期货30%保证金/缓冲。旧IM统一审计的两条30%早盘穿透明细，在用户提供的实际保证金不高于15%口径下，最高资金占用降为{float(breach['operational_morning_capital'].max()):.2%}；既有穿透均消失。该15%上限尚未用经纪商结算单独立验证，因此只解决研究中的资金可行性解释，不构成实盘批准。

## 完整性

- IC主线Call字段最大绝对值：{ic_call_abs_max:.3e}；`has_call`真值行：{ic_has_call_rows}；Call合约非空行：{ic_contract_rows}。
- IM Put/Call随网格错误：{im_integrity['put_scaled_with_grid_errors']}/{im_integrity['call_scaled_with_grid_errors']}；组件隔离最大误差：{im_integrity['full_component_isolation_max_abs']:.3e}。
- 上游三个输出清单全部通过；本版全部{len(checks)}项检查通过。

本记录只冻结研究主线。实盘前仍需实时数据、合约映射、盘口容量、成交冲击、经纪商保证金与异常执行审计。
"""
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text("python freeze_ic_im_system_mainlines_v1.py\n", encoding="utf-8")
    files = sorted(path for path in STAGING.iterdir() if path.name != "output_manifest.json")
    output_manifest = {path.name: {"size": path.stat().st_size, "sha256": sha256(path)} for path in files}
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(STAGING, OUTPUT)
    print(json.dumps({"version": VERSION, "checks": checks, "output": str(OUTPUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

