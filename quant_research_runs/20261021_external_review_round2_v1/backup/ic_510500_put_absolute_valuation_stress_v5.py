from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_rolling_continuous_valuation_v3 as core


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_absolute_valuation_stress_v5"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "66eaa995967bab5109fd8d6e6f481ead9dd8521acf122066cc5384dece0109e6"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_absolute_valuation_stress_v5"

V3_PATH = Path(core.__file__).resolve()
V3_SHA256 = "7b05088caa2ae40358ade307be6fac9728e34c276f04fba97fb110964fc4ebd9"
V2_PATH = Path(core.v2.__file__).resolve()
V2_SHA256 = "b422cfa2cac9bc2db5ab3a1d9be3c6a8aee16d7469a710a618f999cb6e9319e7"

CONDITIONAL_VARIANTS = [
    "abs_base50",
    "abs_stress_high",
    "abs_stress_any",
    "abs_3tier",
]
VARIANTS = ["always_50", "always_100", *CONDITIONAL_VARIANTS]
ALL_VARIANTS = ["no_put", *VARIANTS]
STRUCTURAL_EDGES = [
    ("abs_base50", "abs_stress_high"),
    ("abs_base50", "abs_3tier"),
    ("abs_stress_high", "abs_stress_any"),
    ("abs_stress_high", "abs_3tier"),
]
STRUCTURAL_NEIGHBORS = {
    variant: {
        right if left == variant else left
        for left, right in STRUCTURAL_EDGES
        if variant in {left, right}
    }
    for variant in CONDITIONAL_VARIANTS
}

DEVELOPMENT_END = pd.Timestamp("2020-12-31")
REVISION_START = pd.Timestamp("2021-01-04")
REVISION_END = pd.Timestamp("2024-12-31")
RECENT_START = pd.Timestamp("2025-01-02")
EARLY_DRAWDOWN_START = pd.Timestamp("2021-09-13")
EARLY_DRAWDOWN_END = pd.Timestamp("2021-12-31")
KNOWN_DRAWDOWN_END = pd.Timestamp("2024-02-05")
EXTRA_WINDOWS = ["development", "revision_validation", "recent_expansion"]

SIGNAL_DIAGNOSTICS: dict[str, dict[str, float]] = {}


def sha256(path: Path) -> str:
    return core.sha256(path)


def variant_parameters(variant: str) -> dict[str, object]:
    if variant in {"no_put", "always_50", "always_100"}:
        return {"score_type": "baseline", "overlay_mode": "baseline"}
    modes = {
        "abs_base50": "base50_no_stress",
        "abs_stress_high": "stress_boost_high_only",
        "abs_stress_any": "stress_boost_nonlow",
        "abs_3tier": "absolute_three_tier",
    }
    if variant not in modes:
        raise ValueError(f"Unknown v5 variant: {variant}")
    return {"score_type": "absolute", "overlay_mode": modes[variant]}


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "signal_variant": variant, **variant_parameters(variant)}


def pb_level(value: float) -> int:
    if value < 2.0:
        return 0
    if value < 2.5:
        return 1
    return 2


def erp_level(value: float) -> int:
    if value > 0.03:
        return 0
    if value > 0.015:
        return 1
    return 2


def dividend_level(value: float) -> int:
    if value >= 0.02:
        return 0
    if value >= 0.01:
        return 1
    return 2


def absolute_state(row: pd.Series) -> dict[str, object]:
    pb = pb_level(float(row["pb_aggregate"]))
    erp = erp_level(float(row["erp"]))
    dividend = dividend_level(float(row["trailing_dividend_contribution"]))
    risk = 0.25 * pb + 0.50 * erp + 0.25 * dividend
    if risk < 0.75:
        state = "low"
        code = 0
    elif risk < 1.50:
        state = "medium"
        code = 1
    else:
        state = "high"
        code = 2
    return {
        "pb_level": pb,
        "erp_level": erp,
        "dividend_level": dividend,
        "absolute_risk": float(risk),
        "valuation_state": state,
        "valuation_state_code": code,
    }


def target_for_variant(variant: str, valuation_state: str, stress: bool) -> float:
    if variant == "always_50":
        return 0.5
    if variant == "always_100":
        return 1.0
    if valuation_state == "low":
        return 0.0
    if variant == "abs_base50":
        return 0.5
    if variant == "abs_stress_high":
        return 1.0 if valuation_state == "high" and stress else 0.5
    if variant == "abs_stress_any":
        return 1.0 if stress else 0.5
    if variant == "abs_3tier":
        return 1.0 if valuation_state == "high" else 0.5
    raise ValueError(f"Unknown target variant: {variant}")


def prepare_signal_frame(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    frame = daily_valuation.sort_values("date").copy()
    frame["tri_return"] = frame["tri_close"].pct_change(fill_method=None)
    frame["tri_sma120"] = frame["tri_close"].rolling(120, min_periods=120).mean()
    frame["tri_rv20"] = (
        frame["tri_return"].rolling(20, min_periods=20).std(ddof=0) * math.sqrt(244.0)
    )
    frame["trend_stress"] = frame["tri_close"] < frame["tri_sma120"]
    frame["vol_stress"] = frame["tri_rv20"] >= 0.25
    frame["stress"] = frame["trend_stress"] | frame["vol_stress"]
    formal = frame[frame["date"] >= pd.Timestamp("2015-04-15")]
    if formal[["tri_sma120", "tri_rv20"]].isna().any().any():
        raise RuntimeError("v5 stress feature warmup failure")
    return frame


def build_structural_stability(signals: pd.DataFrame) -> pd.DataFrame:
    active = signals[signals["signal_variant"].isin(CONDITIONAL_VARIANTS)]
    pivot = active.pivot(index="eval_date", columns="signal_variant", values="target_fraction")
    rows: list[dict[str, object]] = []
    for left, right in STRUCTURAL_EDGES:
        rows.append(
            {
                "left_variant": left,
                "right_variant": right,
                **core.active_comparison(pivot[left], pivot[right]),
            }
        )
    return pd.DataFrame(rows)


def build_state_summary(signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for variant, group in signals.groupby("signal_variant", sort=False):
        group = group.sort_values("eval_date")
        target = group["target_fraction"].astype(float)
        rows.append(
            {
                "signal_variant": variant,
                "days": int(len(group)),
                "zero_days": int((target == 0.0).sum()),
                "half_days": int((target == 0.5).sum()),
                "full_days": int((target == 1.0).sum()),
                "average_target_fraction": float(target.mean()),
                "target_transitions": int(target.ne(target.shift()).sum() - 1),
            }
        )
    return pd.DataFrame(rows)


def _capture_signal_diagnostics(signals: pd.DataFrame) -> None:
    SIGNAL_DIAGNOSTICS.clear()
    for variant in CONDITIONAL_VARIANTS:
        group = signals[signals["signal_variant"].eq(variant)].set_index("eval_date")
        early = group.loc[EARLY_DRAWDOWN_START:EARLY_DRAWDOWN_END, "target_fraction"]
        full_path = group.loc[EARLY_DRAWDOWN_START:KNOWN_DRAWDOWN_END, "target_fraction"]
        recent = group.loc[RECENT_START:core.END, "target_fraction"]
        SIGNAL_DIAGNOSTICS[variant] = {
            "early_drawdown_average_target": float(early.mean()),
            "early_drawdown_protected_ratio": float((early > 0).mean()),
            "known_drawdown_average_target": float(full_path.mean()),
            "known_drawdown_protected_ratio": float((full_path > 0).mean()),
            "recent_average_target": float(recent.mean()),
        }


def build_signal_panel(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    states: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    del states
    trade_dates = pd.DatetimeIndex(ic["date"])
    layer_evals = {
        "model": core.proxy.evaluation_dates(
            "daily", core.MODEL_START, core.END, trade_dates, daily_valuation
        ),
        "real": core.proxy.evaluation_dates(
            "daily", core.REAL_START, core.END, trade_dates, daily_valuation
        ),
    }
    unique_days = sorted(set(layer_evals["model"]) | set(layer_evals["real"]))
    signal_frame = prepare_signal_frame(daily_valuation).set_index("date")
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {variant: {} for variant in VARIANTS}
    signal_rows: list[dict[str, object]] = []

    for day in unique_days:
        row = signal_frame.loc[day]
        state = absolute_state(row)
        stress = bool(row["stress"])
        common = {
            **{feature: float(row[feature]) for feature in core.v2.FEATURES},
            "tri_close": float(row["tri_close"]),
            "tri_sma120": float(row["tri_sma120"]),
            "tri_rv20": float(row["tri_rv20"]),
            "trend_stress": bool(row["trend_stress"]),
            "vol_stress": bool(row["vol_stress"]),
            "stress": stress,
            **state,
            "history_months": 0,
        }
        for variant in CONDITIONAL_VARIANTS:
            target = target_for_variant(variant, str(state["valuation_state"]), stress)
            target_lookup[variant][day] = target
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **variant_parameters(variant),
                    **common,
                    "risk_score": float(state["absolute_risk"]),
                    "raw_target_fraction": target,
                    "target_fraction": target,
                }
            )
        for variant, target in [("always_50", 0.5), ("always_100", 1.0)]:
            target_lookup[variant][day] = target
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **variant_parameters(variant),
                    "target_fraction": target,
                }
            )

    schedule_rows: list[dict[str, object]] = []
    for layer, evaluations in layer_evals.items():
        start = core.MODEL_START if layer == "model" else core.REAL_START
        for variant in VARIANTS:
            for sequence, day in enumerate(evaluations):
                execution, initial = core.proxy.next_execution(day, start, trade_dates)
                target = target_lookup[variant][day]
                schedule_rows.append(
                    {
                        "layer": layer,
                        "frequency": "daily",
                        "signal_variant": variant,
                        "sequence": sequence,
                        "eval_date": day,
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target,
                        "three_tier_target_fraction": target,
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v5 execution schedule")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v5 signal execution leakage")

    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    _capture_signal_diagnostics(signals)
    structural = build_structural_stability(signals)
    state_summary = build_state_summary(signals)

    current_rows: list[dict[str, object]] = []
    current = signal_frame.loc[core.END]
    current_state = absolute_state(current)
    for variant in CONDITIONAL_VARIANTS:
        target = target_for_variant(variant, str(current_state["valuation_state"]), bool(current["stress"]))
        current_rows.append(
            {
                "as_of": core.END,
                "signal_variant": variant,
                **variant_parameters(variant),
                **{feature: float(current[feature]) for feature in core.v2.FEATURES},
                "tri_close": float(current["tri_close"]),
                "tri_sma120": float(current["tri_sma120"]),
                "tri_rv20": float(current["tri_rv20"]),
                "trend_stress": bool(current["trend_stress"]),
                "vol_stress": bool(current["vol_stress"]),
                "stress": bool(current["stress"]),
                **current_state,
                "risk_score": float(current_state["absolute_risk"]),
                "research_target_fraction": target,
                "history_months": 0,
                "execution_status": "research_state_only_next_open_unobserved",
            }
        )
    return schedule, signals, structural, state_summary, pd.DataFrame(current_rows)


def segment_slice(
    group: pd.DataFrame, segment: str
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp, bool]:
    first = pd.Timestamp(group["date"].min())
    last = pd.Timestamp(group["date"].max())
    if segment in core.REQUIRED_WINDOWS:
        offset = core.REQUIRED_WINDOWS[segment]
        requested_start = first if offset is None else core.END - offset
        requested_end = core.END
        available = offset is None or first <= requested_start
    elif segment == "development":
        requested_start, requested_end = core.MODEL_START, DEVELOPMENT_END
        available = first <= requested_start and last >= requested_end
    elif segment == "revision_validation":
        requested_start, requested_end = REVISION_START, REVISION_END
        available = first <= requested_start and last >= requested_end
    elif segment == "recent_expansion":
        requested_start, requested_end = RECENT_START, core.END
        available = first <= requested_start and last >= requested_end
    else:
        raise ValueError(segment)
    subset = group[group["date"].between(requested_start, requested_end, inclusive="both")]
    return subset, requested_start, requested_end, available


def _stability_pair(
    structural: pd.DataFrame, left: str, right: str
) -> pd.Series:
    pair = structural[
        structural["left_variant"].isin([left, right])
        & structural["right_variant"].isin([left, right])
    ]
    if len(pair) != 1:
        raise RuntimeError(f"Missing v5 structural pair: {left}, {right}")
    return pair.iloc[0]


def candidate_decisions(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    structural: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model = formal[formal["layer"].eq("model")]
    real = formal[formal["layer"].eq("real")]
    model_base = model[model["signal_variant"].eq("no_put")].set_index("segment")
    real_base = real[real["signal_variant"].eq("no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    rows: list[dict[str, object]] = []

    for variant in CONDITIONAL_VARIANTS:
        model_rows = model[model["signal_variant"].eq(variant)].set_index("segment")
        real_rows = real[real["signal_variant"].eq(variant)].set_index("segment")
        window_cagr = {
            segment: float(
                model_rows.loc[segment, "cash_ann_return"]
                - model_base.loc[segment, "cash_ann_return"]
            )
            for segment in core.REQUIRED_WINDOWS
        }
        window_dd = {
            segment: float(
                model_rows.loc[segment, "cash_max_dd"]
                - model_base.loc[segment, "cash_max_dd"]
            )
            for segment in core.REQUIRED_WINDOWS
        }
        return_pass = all(
            window_cagr[segment]
            >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
            for segment in core.REQUIRED_WINDOWS
        )
        improved_windows = sum(value > 1e-12 for value in window_dd.values())
        dev_cagr = float(
            model_rows.loc["development", "cash_ann_return"]
            - model_base.loc["development", "cash_ann_return"]
        )
        dev_dd = float(
            model_rows.loc["development", "cash_max_dd"]
            - model_base.loc["development", "cash_max_dd"]
        )
        revision_cagr = float(
            model_rows.loc["revision_validation", "cash_ann_return"]
            - model_base.loc["revision_validation", "cash_ann_return"]
        )
        revision_dd = float(
            model_rows.loc["revision_validation", "cash_max_dd"]
            - model_base.loc["revision_validation", "cash_max_dd"]
        )
        recent_cagr = float(
            model_rows.loc["recent_expansion", "cash_ann_return"]
            - model_base.loc["recent_expansion", "cash_ann_return"]
        )
        recent_dd = float(
            model_rows.loc["recent_expansion", "cash_max_dd"]
            - model_base.loc["recent_expansion", "cash_max_dd"]
        )
        real_cagr = float(
            real_rows.loc["full", "cash_ann_return"] - real_base.loc["full", "cash_ann_return"]
        )
        real_dd = float(
            real_rows.loc["full", "cash_max_dd"] - real_base.loc["full", "cash_max_dd"]
        )
        model_days = int(exposure_lookup.loc[f"model_{variant}", "protected_days"])
        real_days = int(exposure_lookup.loc[f"real_{variant}", "protected_days"])
        diagnostics = SIGNAL_DIAGNOSTICS[variant]
        individual = bool(
            dev_dd >= 0.03
            and dev_cagr >= -0.01
            and revision_dd >= 0.03
            and revision_cagr >= -0.01
            and recent_cagr >= -0.01
            and recent_dd >= -0.01
            and improved_windows >= 3
            and return_pass
            and real_dd >= 0.005
            and real_cagr >= -0.01
            and model_days >= 20
            and real_days >= 20
            and diagnostics["early_drawdown_average_target"] >= 0.50
        )
        rows.append(
            {
                "signal_variant": variant,
                **variant_parameters(variant),
                "development_cagr_delta": dev_cagr,
                "development_dd_improvement": dev_dd,
                "revision_cagr_delta": revision_cagr,
                "revision_dd_improvement": revision_dd,
                "recent_cagr_delta": recent_cagr,
                "recent_dd_improvement": recent_dd,
                "real_cagr_delta": real_cagr,
                "real_dd_improvement": real_dd,
                "improved_required_windows": improved_windows,
                "return_tolerance_pass": return_pass,
                "model_protected_days": model_days,
                "real_protected_days": real_days,
                "average_target_fraction": float(
                    exposure_lookup.loc[f"model_{variant}", "average_target_fraction"]
                ),
                **diagnostics,
                "single_candidate_pass": individual,
            }
        )

    decisions = pd.DataFrame(rows)
    pass_lookup = decisions.set_index("signal_variant")["single_candidate_pass"].to_dict()
    support_rows: list[dict[str, object]] = []
    for variant in CONDITIONAL_VARIANTS:
        supporting: list[str] = []
        for neighbor in STRUCTURAL_NEIGHBORS[variant]:
            if not pass_lookup.get(neighbor, False):
                continue
            pair = _stability_pair(structural, variant, neighbor)
            if (
                float(pair["protected_day_jaccard"]) >= 0.60
                and float(pair["active_target_mae"]) <= 0.25
            ):
                supporting.append(neighbor)
        support_rows.append(
            {
                "signal_variant": variant,
                "structural_neighbor_pass": bool(supporting),
                "supporting_neighbors": ";".join(sorted(supporting)),
                "all_preregistered_pass": bool(
                    pass_lookup.get(variant, False) and supporting
                ),
            }
        )
    decisions = decisions.merge(pd.DataFrame(support_rows), on="signal_variant", validate="one_to_one")
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    if passed.empty:
        summary = {
            "decision": "keep_default",
            "stability_label": "reject",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    else:
        if "abs_stress_any" in set(passed["signal_variant"]):
            selected = "abs_stress_any"
        else:
            selected = str(
                passed.sort_values(["average_target_fraction", "signal_variant"]).iloc[0][
                    "signal_variant"
                ]
            )
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(passed) >= 3 else "narrow_stable",
            "selected_variant": selected,
            "passing_candidates": passed["signal_variant"].tolist(),
            "sample_reuse": "not_independent_oos",
        }
    return decisions, summary


def build_record(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    decisions: pd.DataFrame,
    decision_summary: dict[str, object],
    current: pd.DataFrame,
    cross_stats: dict[str, object],
    qvix_stats: dict[str, object],
) -> str:
    del exposure
    full = formal[
        formal["layer"].eq("model")
        & formal["segment"].isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])
        & formal["available"].eq(True)
    ][["candidate", "segment", "cash_ann_return", "cash_max_dd"]]
    decision_cols = [
        "signal_variant",
        "development_cagr_delta",
        "development_dd_improvement",
        "revision_cagr_delta",
        "revision_dd_improvement",
        "recent_cagr_delta",
        "recent_dd_improvement",
        "real_cagr_delta",
        "real_dd_improvement",
        "all_preregistered_pass",
    ]
    current_cols = [
        "signal_variant",
        "absolute_risk",
        "valuation_state",
        "trend_stress",
        "vol_stress",
        "research_target_fraction",
    ]
    lines = [
        "# IC + 510500 Put 固定绝对估值与压力升级保护 v5",
        "",
        "> 研究回测；未获准实盘；2021—2026已用于规则修订，不是独立OOS。",
        "",
        "## 决定",
        "",
        f"- 决定：`{decision_summary['decision']}`。",
        f"- 稳定性：`{decision_summary['stability_label']}`。",
        f"- 观察线：`{decision_summary['selected_variant']}`。",
        "",
        "## 模型层强制窗口",
        "",
        full.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断",
        "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 2026-08-14研究状态",
        "",
        current[current_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 验证与限制",
        "",
        f"- QVIX代理校验：{'通过' if qvix_stats['passed'] else '未通过'}。",
        f"- 实际/模型CAGR差方向一致率：{cross_stats['same_cagr_direction_ratio']:.2%}。",
        f"- 实际/模型回撤改善方向一致率：{cross_stats['same_dd_direction_ratio']:.2%}。",
        "- 固定绝对边界与压力规则均在任何v5收益计算前冻结。",
        "- 2015—2022为模型Put；真实层是第三方日线，不代表可成交盘口。",
        "- 当前目标只用于研究审计，不是订单。",
    ]
    return "\n".join(lines) + "\n"


def configure_core() -> None:
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = VARIANTS
    core.ALL_VARIANTS = ALL_VARIANTS
    core.ECON_VARIANTS = CONDITIONAL_VARIANTS
    core.STRUCTURAL_VARIANT = "abs_stress_any"
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.build_signal_panel = build_signal_panel
    core.segment_slice = segment_slice
    core.candidate_decisions = candidate_decisions
    core.build_record = build_record
    core.__file__ = str(Path(__file__).resolve())


def augment_manifests() -> None:
    manifest_path = OUTPUT / "data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample"] = {
        "model": [str(core.MODEL_START.date()), str(core.END.date())],
        "real": [str(core.REAL_START.date()), str(core.END.date())],
        "development": [str(core.MODEL_START.date()), str(DEVELOPMENT_END.date())],
        "revision_validation": [str(REVISION_START.date()), str(REVISION_END.date())],
        "recent_expansion": [str(RECENT_START.date()), str(core.END.date())],
    }
    manifest["framework_dependencies"] = {
        "common_harness": {
            "path": str(V3_PATH.relative_to(ROOT)),
            "sha256": V3_SHA256,
        },
        "valuation_loader": {
            "path": str(V2_PATH.relative_to(ROOT)),
            "sha256": V2_SHA256,
        },
    }
    manifest["source_hashes"][str(V3_PATH.relative_to(ROOT))] = V3_SHA256
    manifest["signal_definition"] = {
        "pb_levels": [2.0, 2.5],
        "erp_levels": [0.015, 0.03],
        "dividend_levels": [0.01, 0.02],
        "absolute_state_levels": [0.75, 1.5],
        "trend_sma_days": 120,
        "realized_vol_days": 20,
        "realized_vol_threshold": 0.25,
    }
    manifest["warnings"].append(
        "v5 was designed after v4 full-history results; 2021-2026 is not independent OOS."
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    correct_commands = (
        "python.exe -m pytest test_ic_510500_put_absolute_valuation_stress_v5.py -q\n"
        "python.exe ic_510500_put_absolute_valuation_stress_v5.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(correct_commands, encoding="utf-8")
    scan_log = SCAN / "command_log.txt"
    log_text = scan_log.read_text(encoding="utf-8")
    log_text = log_text.replace(
        "python.exe -m pytest test_ic_510500_put_rolling_continuous_valuation_v3.py -q\n"
        "python.exe ic_510500_put_rolling_continuous_valuation_v3.py\n",
        correct_commands,
    )
    scan_log.write_text(log_text, encoding="utf-8")

    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["source_hashes"] = manifest["source_hashes"]
    meta["dependencies"] = manifest["framework_dependencies"]
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if sha256(V3_PATH) != V3_SHA256:
        raise RuntimeError("Frozen v3 common framework dependency changed")
    if sha256(V2_PATH) != V2_SHA256:
        raise RuntimeError("Frozen v2 valuation-loader dependency changed")
    configure_core()
    core.main()
    augment_manifests()


if __name__ == "__main__":
    main()
