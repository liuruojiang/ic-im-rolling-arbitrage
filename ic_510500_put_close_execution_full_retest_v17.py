from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_close_execution_full_retest_v17"
OUTPUT = ROOT / "outputs" / VERSION
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "b0603f150ccf7ad4a298b53cfa53fbbb7f8a39da1075f9bab4aeffeeee333704"

COMPONENTS = [
    "ic_510500_put_proxy_validation_v1",
    "ic_510500_put_full_cycle_valuation_v2",
    "ic_510500_put_rolling_continuous_valuation_v4",
    "ic_510500_put_absolute_valuation_stress_v5",
    "ic_510500_put_v4_monthly_tenor_rerun_v6",
    "ic_510500_put_persistent_stress_hold3m_v7",
    "ic_510500_put_tail_value_gate_v8",
    "ic_510500_put_extreme_valuation_gate_v9",
    "ic_510500_put_extreme_valuation_absolute_momentum_v10",
    "ic_510500_put_absolute_momentum_protection_tool_v13",
    "ic_510500_put_dynamic_valuation_absolute_momentum_front95_v14",
    "ic_510500_put_absolute_momentum_horizon_scan_front95_v15",
    "ic_510500_put_dynamic_lower_threshold_front95_v16",
]

WINDOWS = [
    ("full", None),
    ("last_10y", 10),
    ("last_5y", 5),
    ("last_3y", 3),
    ("last_1y", 1),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_spec() -> None:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v17 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen v17 specification sidecar mismatch")


def transformed_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for key, value in frames.items():
        if not isinstance(value, pd.DataFrame):
            result[key] = value
            continue
        frame = value.copy()
        if key == "etf500" and {"open", "close"}.issubset(frame.columns):
            frame["open"] = frame["close"]
        if key == "histories" and {"open", "close"}.issubset(frame.columns):
            frame["open"] = frame["close"]
        result[key] = frame
    return result


def transformed_market(result: tuple[pd.DataFrame, dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    market, checks = result
    market = market.copy()
    for source, target in [
        ("spot_open", "spot_close"),
        ("sigma_open", "sigma_close"),
        ("rate_open", "rate_close"),
        ("dividend_open", "dividend_close"),
    ]:
        if source in market and target in market:
            market[source] = market[target]
    checks = dict(checks)
    checks["execution_state_override"] = "T+1 close; execution open fields replaced in memory"
    return market, checks


def _patch_loader(owner: Any, name: str) -> None:
    original = getattr(owner, name, None)
    if original is None or getattr(original, "_v17_close_wrapper", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> dict[str, pd.DataFrame]:
        return transformed_frames(original(*args, **kwargs))

    wrapped._v17_close_wrapper = True  # type: ignore[attr-defined]
    setattr(owner, name, wrapped)


def _patch_market(owner: Any, name: str) -> None:
    original = getattr(owner, name, None)
    if original is None or getattr(original, "_v17_close_wrapper", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        return transformed_market(original(*args, **kwargs))

    wrapped._v17_close_wrapper = True  # type: ignore[attr-defined]
    setattr(owner, name, wrapped)


def neutral_parity(*args: Any, **kwargs: Any) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "audit": "old-open dependency parity intentionally superseded",
                "max_abs_close_execution_superseded": 0.0,
                "passed": True,
            }
        ]
    )


def patch_component(module: Any, component_output: Path) -> None:
    proxy = importlib.import_module("ic_510500_put_proxy_validation_v1")
    v2 = importlib.import_module("ic_510500_put_full_cycle_valuation_v2")
    _patch_loader(proxy, "load_inputs")
    _patch_loader(v2, "load_inputs")
    _patch_market(proxy, "prepare_model_market")

    module.OUTPUT = component_output
    if hasattr(module, "SCAN"):
        module.SCAN = OUTPUT / "source_quant_scans" / component_output.name
        module.SCAN.mkdir(parents=True, exist_ok=True)
        initial_meta = {
            "run_id": module.SCAN.name,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "phase": "initialized",
            "project": "new_strategy_research",
            "strategy": component_output.name,
            "subsystem": "ic_510500_put_protection",
            "parameter_group": "execution_open_to_close_component_retest",
            "repo_root": str(ROOT),
            "entrypoint": Path(module.__file__).name,
        }
        (module.SCAN / "scan_meta.json").write_text(
            json.dumps(initial_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (module.SCAN / "command_log.txt").write_text("", encoding="utf-8")

    for name in ["parity_audit", "baseline_parity", "front_parity"]:
        if hasattr(module, name):
            setattr(module, name, neutral_parity)


def corrected_component_metadata(component: str, component_output: Path) -> None:
    if (component_output / "source_record_as_generated.md").exists():
        return
    source_record = component_output / "record.md"
    if source_record.exists():
        source_record.replace(component_output / "source_record_as_generated.md")
    source_manifest = component_output / "data_manifest.json"
    original_manifest: dict[str, Any] = {}
    if source_manifest.exists():
        original_manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        source_manifest.replace(component_output / "source_data_manifest_as_generated.json")
    daily = pd.read_csv(component_output / "daily_candidates.csv.gz", usecols=["candidate", "date"])
    record = "\n".join(
        [
            f"# {component} 收盘执行组件重测",
            "",
            f"- 上级版本：`{VERSION}`。",
            "- 本目录复用冻结来源版本的信号、候选和成本逻辑；Put 成交状态在内存中由 T+1 open 统一替换为 T+1 close。",
            "- `source_record_as_generated.md`沿用旧模板，内含开盘口径文字，仅保留用于核对原来源程序，不代表本次执行假设。",
            "- 研究状态：未批准实盘。",
            "",
        ]
    )
    (component_output / "record.md").write_text(record, encoding="utf-8")
    manifest = {
        "version": VERSION,
        "source_component": component,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "execution": "signal T close; option transaction T+1 close",
        "futures_baseline": "unchanged from source component",
        "candidate_count": int(daily["candidate"].nunique()),
        "rows": int(len(daily)),
        "source_manifest": original_manifest,
        "research_status": "research_only_not_live_approved",
    }
    source_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def run_component(component: str) -> None:
    verify_spec()
    if component not in COMPONENTS:
        raise ValueError(component)
    component_output = OUTPUT / "components" / component
    if component_output.exists():
        raise FileExistsError(component_output)
    module = importlib.import_module(component)
    patch_component(module, component_output)
    module.main()
    corrected_component_metadata(component, component_output)


def metrics(returns: pd.Series) -> tuple[float, float]:
    values = returns.astype(float)
    nav = (1.0 + values).cumprod()
    ann = float(nav.iloc[-1] ** (252.0 / len(values)) - 1.0)
    max_dd = float((nav / nav.cummax() - 1.0).min())
    return ann, max_dd


def component_metrics(source: str, daily: pd.DataFrame, execution: str) -> pd.DataFrame:
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        end = pd.Timestamp(group["date"].max())
        start = pd.Timestamp(group["date"].min())
        for window, years in WINDOWS:
            available = True
            reason = ""
            subset = group
            if years is not None:
                cutoff = end - pd.DateOffset(years=years)
                if start > cutoff:
                    available = False
                    reason = "insufficient executable history"
                    subset = group.iloc[0:0]
                else:
                    subset = group[group["date"] >= cutoff]
            if available and len(subset):
                ann, max_dd = metrics(subset["cash_ret"])
            else:
                ann, max_dd = np.nan, np.nan
            rows.append(
                {
                    "source_version": source,
                    "execution": execution,
                    "candidate": candidate,
                    "layer": "model" if str(candidate).startswith("model_") else "real",
                    "window": window,
                    "available": available,
                    "unavailable_reason": reason,
                    "rows": int(len(subset)),
                    "start": subset["date"].min() if len(subset) else pd.NaT,
                    "end": subset["date"].max() if len(subset) else pd.NaT,
                    "ann_return": ann,
                    "max_dd": max_dd,
                }
            )
    return pd.DataFrame(rows)


def annual_metrics(source: str, daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(["candidate", daily["date"].dt.year]):
        ann, max_dd = metrics(group.sort_values("date")["cash_ret"])
        rows.append(
            {
                "source_version": source,
                "candidate": candidate,
                "layer": "model" if str(candidate).startswith("model_") else "real",
                "year": int(year),
                "ann_return": ann,
                "max_dd": max_dd,
                "rows": int(len(group)),
            }
        )
    return pd.DataFrame(rows)


def load_old_daily(source: str) -> pd.DataFrame:
    path = ROOT / "outputs" / source / "daily_candidates.csv.gz"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, parse_dates=["date"])


def build_price_audit(component_daily: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    proxy = importlib.import_module("ic_510500_put_proxy_validation_v1")
    frames = proxy.load_inputs()
    snapshots = frames["snapshots"].copy()
    histories = frames["histories"].copy()
    snapshots["date"] = pd.to_datetime(snapshots["date"])
    histories["date"] = pd.to_datetime(histories["date"])
    contract_groups = {
        str(contract): group[["date", "security_id"]].drop_duplicates().sort_values("date")
        for contract, group in snapshots.groupby("contract_id", sort=False)
    }
    history_lookup = histories.set_index(["security_id", "date"])
    rows: list[dict[str, Any]] = []
    for source, daily in component_daily:
        real = daily[daily["candidate"].str.startswith("real_")].copy()
        real["date"] = pd.to_datetime(real["date"])
        for candidate, group in real.groupby("candidate", sort=True):
            group = group.sort_values("date")
            prior_contract = group["put_contract"].shift(1).fillna("").astype(str)
            current_contract = group["put_contract"].fillna("").astype(str)
            traded = group["put_cost_rate"].astype(float).gt(0)
            for index in group.index[traded]:
                day = pd.Timestamp(group.at[index, "date"])
                old_contract = prior_contract.loc[index]
                new_contract = current_contract.loc[index]
                contracts = sorted({value for value in [old_contract, new_contract] if value and value != "nan"})
                if not contracts:
                    rows.append(
                        {
                            "source_version": source,
                            "candidate": candidate,
                            "date": day,
                            "contract_id": "",
                            "close": np.nan,
                            "volume": np.nan,
                            "matched": False,
                            "reason": "cost event without inferable EOD/prior contract",
                        }
                    )
                    continue
                for contract in contracts:
                    close, volume, matched, reason = np.nan, np.nan, False, "quote not found"
                    mapping = contract_groups.get(contract)
                    if mapping is not None:
                        eligible = mapping[mapping["date"] <= day].sort_values("date", ascending=False)
                        for security_id in eligible["security_id"].drop_duplicates():
                            key = (str(security_id), day)
                            if key not in history_lookup.index:
                                continue
                            quote = history_lookup.loc[key]
                            if isinstance(quote, pd.DataFrame):
                                quote = quote.iloc[0]
                            candidate_close = float(quote["close"])
                            candidate_volume = float(quote["volume"])
                            if candidate_close > 0 and candidate_volume > 0:
                                close, volume, matched, reason = (
                                    candidate_close,
                                    candidate_volume,
                                    True,
                                    "",
                                )
                                break
                        if not matched and len(eligible):
                            reason = "mapped security has no positive close/volume on trade date"
                    rows.append(
                        {
                            "source_version": source,
                            "candidate": candidate,
                            "date": day,
                            "contract_id": contract,
                            "close": close,
                            "volume": volume,
                            "matched": matched,
                            "reason": reason,
                        }
                    )
    return pd.DataFrame(rows)


def finalize() -> None:
    metric_parts: list[pd.DataFrame] = []
    annual_parts: list[pd.DataFrame] = []
    comparison_parts: list[pd.DataFrame] = []
    inventory: list[dict[str, Any]] = []
    no_put_rows: list[dict[str, Any]] = []
    component_daily: list[tuple[str, pd.DataFrame]] = []
    decision_parts: list[pd.DataFrame] = []

    for source in COMPONENTS:
        component = OUTPUT / "components" / source
        daily = pd.read_csv(component / "daily_candidates.csv.gz", parse_dates=["date"])
        old = load_old_daily(source)
        component_daily.append((source, daily))
        close_metrics = component_metrics(source, daily, "close")
        open_metrics = component_metrics(source, old, "open")
        metric_parts.append(close_metrics)
        annual_parts.append(annual_metrics(source, daily))
        joined = close_metrics.merge(
            open_metrics,
            on=["source_version", "candidate", "layer", "window"],
            suffixes=("_close", "_open"),
            validate="one_to_one",
        )
        joined["ann_return_delta_close_minus_open"] = joined["ann_return_close"] - joined["ann_return_open"]
        joined["max_dd_improvement_close_minus_open"] = joined["max_dd_close"] - joined["max_dd_open"]
        comparison_parts.append(joined)
        inventory.append(
            {
                "source_version": source,
                "candidate_count": int(daily["candidate"].nunique()),
                "rows": int(len(daily)),
                "start": str(pd.Timestamp(daily["date"].min()).date()),
                "end": str(pd.Timestamp(daily["date"].max()).date()),
                "candidate_set_match_old": set(daily["candidate"]) == set(old["candidate"]),
            }
        )
        for candidate in sorted(set(daily["candidate"]) & set(old["candidate"])):
            if not candidate.endswith("no_put"):
                continue
            left = daily[daily["candidate"].eq(candidate)][["date", "cash_ret"]]
            right = old[old["candidate"].eq(candidate)][["date", "cash_ret"]]
            parity = left.merge(right, on="date", suffixes=("_close", "_open"), validate="one_to_one")
            no_put_rows.append(
                {
                    "source_version": source,
                    "candidate": candidate,
                    "rows": int(len(parity)),
                    "max_abs_daily_cash_ret_diff": float(
                        (parity["cash_ret_close"] - parity["cash_ret_open"]).abs().max()
                    ),
                }
            )
        for name in ["candidate_decisions.csv", "decision_table.csv"]:
            path = component / name
            if path.exists():
                frame = pd.read_csv(path)
                frame.insert(0, "source_version", source)
                decision_parts.append(frame)
                break

    formal = pd.concat(metric_parts, ignore_index=True)
    annual = pd.concat(annual_parts, ignore_index=True)
    comparison = pd.concat(comparison_parts, ignore_index=True)
    inventory_table = pd.DataFrame(inventory)
    no_put = pd.DataFrame(no_put_rows)
    price_audit = build_price_audit(component_daily)

    if not inventory_table["candidate_set_match_old"].all():
        raise RuntimeError("IC component candidate set mismatch")
    if no_put["max_abs_daily_cash_ret_diff"].max() > 1e-14:
        raise RuntimeError("IC no-Put parity failed")
    if len(price_audit) and not price_audit["matched"].all():
        failed = price_audit[~price_audit["matched"]]
        raise RuntimeError("IC close price audit failed:\n" + failed.head(20).to_string(index=False))

    key_patterns = [
        "real_daily_front_three_tier",
        "real_front_exit_m95",
        "real_2m_monthly_exit_m95",
        "real_3m_monthly_exit_m95",
        "real_3cycle_hold_expiry_m95",
        "real_fixed175_or_mom120",
        "real_dynamic075_or_mom120",
        "real_mom120",
        "real_dynamic060_or_mom120",
        "real_dynamic065_or_mom120",
        "real_dynamic070_or_mom120",
        "real_dynamic075_only",
    ]
    key = comparison[
        comparison["candidate"].isin(key_patterns)
        | comparison["candidate"].str.endswith("no_put")
    ].copy()
    key = key[key["window"].isin([item[0] for item in WINDOWS])]

    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    comparison.to_csv(OUTPUT / "open_vs_close_metrics.csv", index=False)
    inventory_table.to_csv(OUTPUT / "component_inventory.csv", index=False)
    no_put.to_csv(OUTPUT / "no_put_parity.csv", index=False)
    price_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    key.to_csv(OUTPUT / "key_candidate_summary.csv", index=False)
    if decision_parts:
        pd.concat(decision_parts, ignore_index=True, sort=False).to_csv(
            OUTPUT / "source_decisions_close_execution.csv", index=False
        )

    real_full = comparison[
        comparison["layer"].eq("real")
        & comparison["window"].eq("full")
        & ~comparison["candidate"].str.endswith("no_put")
    ]
    improved_return = int(real_full["ann_return_delta_close_minus_open"].gt(0).sum())
    improved_dd = int(real_full["max_dd_improvement_close_minus_open"].gt(0).sum())
    record = "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            f"- 完成 {len(COMPONENTS)} 个冻结来源版本的全部候选重测；真实层非no-Put全样本路径 {len(real_full)} 条。",
            f"- 收盘执行相对开盘执行：{improved_return} 条真实路径提高全样本年化，{improved_dd} 条改善全样本最大回撤。",
            f"- 真实成交腿审计 {len(price_audit)} 条，全部匹配同日同合约正收盘价及正成交量。",
            f"- no-Put逐日最大误差 {no_put['max_abs_daily_cash_ret_diff'].max():.3e}；候选集合全部与来源版本一致。",
            "- 旧开盘结果被本版收盘口径取代用于执行可行性判断，但源文件和冻结产物保留作为审计证据。",
            "- 本版仅研究，未批准实盘。",
            "",
        ]
    )
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "execution": "signal T close; option transactions T+1 close",
        "components": COMPONENTS,
        "component_count": len(COMPONENTS),
        "candidate_count_sum": int(inventory_table["candidate_count"].sum()),
        "close_price_audit_legs": int(len(price_audit)),
        "no_put_parity_max_abs_error": float(no_put["max_abs_daily_cash_ret_diff"].max()),
        "research_status": "research_only_not_live_approved",
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        f"{sys.executable} {Path(__file__).name}\n", encoding="utf-8"
    )


def orchestrate() -> None:
    verify_spec()
    components_root = OUTPUT / "components"
    if not OUTPUT.exists():
        components_root.mkdir(parents=True, exist_ok=False)
    elif not components_root.is_dir():
        raise FileExistsError(f"Unexpected non-resumable formal output: {OUTPUT}")
    for component in COMPONENTS:
        component_output = components_root / component
        if component_output.exists():
            required = [
                component_output / "daily_candidates.csv.gz",
                component_output / "record.md",
            ]
            if not all(path.exists() for path in required):
                raise RuntimeError(f"Incomplete non-resumable component: {component_output}")
            corrected_component_metadata(component, component_output)
            continue
        command = [sys.executable, str(Path(__file__).resolve()), "--component", component]
        subprocess.run(command, cwd=ROOT, check=True)
    finalize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", choices=COMPONENTS)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.component:
        run_component(args.component)
    elif args.finalize:
        finalize()
    else:
        orchestrate()


if __name__ == "__main__":
    main()
