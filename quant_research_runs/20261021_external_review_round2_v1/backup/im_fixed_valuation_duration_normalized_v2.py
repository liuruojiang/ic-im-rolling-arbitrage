from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_fixed_valuation_unbounded_score_v6 as ic_v6

ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_duration_normalized_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "8b623e3ee8f061bdc54efdf845ee4a518c588882550d6658887e33ac6abeded8"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V1_OUTPUT = ROOT / "outputs" / "im_fixed_valuation_unbounded_transfer_v1"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_1000_im_fixed_valuation_duration_normalized_v2_valuation_body_duration_normalized_episode_gate"
)

INPUT_HASHES = {
    ROOT / "im_fixed_valuation_unbounded_transfer_v1.py": "56549fc43b1fd70cc1f0dafe1a5b0b6895b1bbcb82529122eaa6e9a70f6bfda3",
    ROOT / "docs" / "im_fixed_valuation_unbounded_transfer_v1_spec.md": "8d3eeec74f09588dcb1425b88e29adee9e4c1df7901108d9d2b1f254c0734007",
    ROOT / "docs" / "im_fixed_valuation_unbounded_transfer_v1_postrun_audit.md": "6088e49637c6d3e33c818b9c58f2fa3e8f44861206a9a5967dff091f278a2497",
    V1_OUTPUT / "daily_unbounded_fixed_scores.csv.gz": "1e186ffc943ebcc16769cb86c79fd817bb1d754660f90d8d8a4b9d74a479a49f",
    V1_OUTPUT / "monthly_unbounded_fixed_scores.csv": "1b173ae29df570825836af7c9c97b6c851254bc7eca8dd91fc45af6546db3cbc",
    V1_OUTPUT / "economic_boundary.csv": "fb300003bc512054b79b47c0f722d1d0bb50a48b95ec30b0172a92317cffb065",
    V1_OUTPUT / "factor_structure_summary.csv": "37536c528c113f6982e91d7ca9c46262ab7ec5df90e474416d15917a14ef201b",
    V1_OUTPUT / "price_index_context.csv": "1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c",
    V1_OUTPUT / "raw_threshold_map.csv": "32748e84e963643bd6000671067f7be81f17775cbe05f2268c10d240543c0465",
    V1_OUTPUT / "threshold_selection.csv": "a0da0b7f6023ff8e276721cc4dfe8cce178cb443fd57820b6dccdc7efb7b2da6",
    V1_OUTPUT / "vintage_invariance.csv": "7d14106574cc389e54a556b546598c3bec61ef17b2212e37e9563201ccae2ffe",
    V1_OUTPUT / "integrity_checks.json": "1b8fc2b4f3984cc53b2f94ccb6c64ab234c642af9414a701baff9c7aec2c6967",
    V1_OUTPUT / "decision_summary.json": "49c89290091dd4cc6a1f5f2213eb01cd6ccee3d6db5aa9b800aa8e89b7130fbe",
    V1_OUTPUT / "output_manifest.json": "1f0dad5e100d135e1748cd30629e0e84d04f2ae44cd4de71b6c607e04071a2d7",
}

IC_FULL_MONTHS = 236
IC_FULL_MIN_EPISODES = 5
IC_RECENT10_MONTHS = 121
IC_RECENT10_MIN_EPISODES = 2
EXPECTED_DAILY_ROWS = 2634
EXPECTED_MONTHLY_ROWS = 131
EXPECTED_RECENT10_MONTHS = 121


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def verify_v1_manifest() -> None:
    manifest = json.loads(
        (V1_OUTPUT / "output_manifest.json").read_text(encoding="utf-8")
    )
    for name, item in manifest["files"].items():
        path = V1_OUTPUT / name
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen v1 output manifest mismatch: {path}")


def verify_frozen_inputs(*, require_fresh_output: bool) -> dict[str, str]:
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v2 specification mismatch")
    sidecar_hash = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar_hash != SPEC_HASH:
        raise RuntimeError("Frozen v2 specification sidecar mismatch")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v2 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v2 parameter scan folder is missing")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v2 input changed: {path}")
    verify_v1_manifest()
    return {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()}


def load_inputs() -> dict[str, Any]:
    daily = pd.read_csv(
        V1_OUTPUT / "daily_unbounded_fixed_scores.csv.gz", parse_dates=["date"]
    )
    monthly = pd.read_csv(
        V1_OUTPUT / "monthly_unbounded_fixed_scores.csv", parse_dates=["date"]
    )
    return {
        "daily": daily,
        "monthly": monthly,
        "economic": pd.read_csv(V1_OUTPUT / "economic_boundary.csv"),
        "factor_summary": pd.read_csv(
            V1_OUTPUT / "factor_structure_summary.csv"
        ),
        "price_context_v1": pd.read_csv(V1_OUTPUT / "price_index_context.csv"),
        "threshold_map": pd.read_csv(V1_OUTPUT / "raw_threshold_map.csv"),
        "selection_v1": pd.read_csv(V1_OUTPUT / "threshold_selection.csv"),
        "vintage": pd.read_csv(
            V1_OUTPUT / "vintage_invariance.csv", parse_dates=["vintage_date"]
        ),
        "decision_v1": json.loads(
            (V1_OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
        ),
    }


def duration_requirements(monthly: pd.DataFrame) -> dict[str, Any]:
    anchor = monthly["date"].max()
    recent_mask = monthly["date"] >= anchor - pd.DateOffset(years=10)
    full_months = len(monthly)
    recent_months = int(recent_mask.sum())
    split_index = math.ceil(full_months / 2)
    return {
        "full_months": full_months,
        "recent10_months": recent_months,
        "required_full_episodes": math.ceil(
            full_months * IC_FULL_MIN_EPISODES / IC_FULL_MONTHS
        ),
        "required_recent10_episodes": math.ceil(
            recent_months * IC_RECENT10_MIN_EPISODES / IC_RECENT10_MONTHS
        ),
        "split_index": split_index,
        "early_months": split_index,
        "late_months": full_months - split_index,
        "early_start": monthly["date"].iloc[0],
        "early_end": monthly["date"].iloc[split_index - 1],
        "late_start": monthly["date"].iloc[split_index],
        "late_end": monthly["date"].iloc[-1],
        "recent_mask": recent_mask,
    }


def gate_definition(requirements: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ic_full_reference_months": IC_FULL_MONTHS,
                "ic_full_reference_min_episodes": IC_FULL_MIN_EPISODES,
                "im_full_months": requirements["full_months"],
                "im_required_full_episodes": requirements[
                    "required_full_episodes"
                ],
                "ic_recent10_reference_months": IC_RECENT10_MONTHS,
                "ic_recent10_reference_min_episodes": IC_RECENT10_MIN_EPISODES,
                "im_recent10_months": requirements["recent10_months"],
                "im_required_recent10_episodes": requirements[
                    "required_recent10_episodes"
                ],
                "rounding": "ceil",
                "early_months": requirements["early_months"],
                "late_months": requirements["late_months"],
                "early_start": requirements["early_start"].date().isoformat(),
                "early_end": requirements["early_end"].date().isoformat(),
                "late_start": requirements["late_start"].date().isoformat(),
                "late_end": requirements["late_end"].date().isoformat(),
                "temporal_breadth_rule": (
                    "at_least_one_global_episode_start_in_each_disjoint_half"
                ),
            }
        ]
    )


def temporal_episode_row(
    monthly: pd.DataFrame,
    active: pd.Series,
    family: str,
    threshold: float,
    split_index: int,
) -> dict[str, Any]:
    values = active.astype(bool).to_numpy()
    starts = values & ~np.r_[False, values[:-1]]
    positions = np.flatnonzero(starts)
    early_positions = positions[positions < split_index]
    late_positions = positions[positions >= split_index]
    dates = monthly["date"].reset_index(drop=True)

    def date_text(items: np.ndarray) -> str:
        return "|".join(dates.iloc[items].dt.strftime("%Y-%m-%d"))

    return {
        "family": family,
        "threshold": float(threshold),
        "full_episode_starts": len(positions),
        "early_episode_starts": len(early_positions),
        "late_episode_starts": len(late_positions),
        "early_start_dates": date_text(early_positions),
        "late_start_dates": date_text(late_positions),
        "temporal_breadth_pass": bool(
            len(early_positions) >= 1 and len(late_positions) >= 1
        ),
    }


def make_threshold_selection(
    monthly: pd.DataFrame,
    economic: pd.DataFrame,
    requirements: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    recent_mask = requirements["recent_mask"]
    economic_index = economic.set_index(["family", "threshold"])
    rows: list[dict[str, Any]] = []
    temporal_rows: list[dict[str, Any]] = []
    for threshold in ic_v6.THRESHOLDS:
        mean_active = monthly["unbounded_mean_knot"].ge(threshold)
        median_active = monthly["unbounded_median_knot"].ge(threshold)
        mean_full_episodes, mean_longest = ic_v6.episode_stats(mean_active)
        median_full_episodes, median_longest = ic_v6.episode_stats(median_active)
        mean_recent_episodes = ic_v6.recent_episode_count(mean_active, recent_mask)
        median_recent_episodes = ic_v6.recent_episode_count(
            median_active, recent_mask
        )
        mean_recent_ratio = float(mean_active.loc[recent_mask].mean())
        median_recent_ratio = float(median_active.loc[recent_mask].mean())
        mean_econ = economic_index.loc[("unbounded_mean", threshold)]
        median_econ = economic_index.loc[("unbounded_median", threshold)]
        full_jaccard = ic_v6.jaccard(mean_active, median_active)
        recent_jaccard = ic_v6.jaccard(
            mean_active.loc[recent_mask], median_active.loc[recent_mask]
        )
        recent_difference = abs(mean_recent_ratio - median_recent_ratio)
        mean_temporal = temporal_episode_row(
            monthly,
            mean_active,
            "unbounded_mean",
            float(threshold),
            requirements["split_index"],
        )
        median_temporal = temporal_episode_row(
            monthly,
            median_active,
            "unbounded_median",
            float(threshold),
            requirements["split_index"],
        )
        temporal_rows.extend([mean_temporal, median_temporal])
        gates = {
            "gate_mean_duration_normalized_episode_count": bool(
                mean_full_episodes >= requirements["required_full_episodes"]
                and mean_recent_episodes
                >= requirements["required_recent10_episodes"]
            ),
            "gate_mean_temporal_breadth": mean_temporal[
                "temporal_breadth_pass"
            ],
            "gate_mean_recent_tail_coverage": bool(
                0.05 <= mean_recent_ratio <= 0.30
            ),
            "gate_mean_local_boundary_sample": bool(
                int(mean_econ["local_months"]) >= 8
            ),
            "gate_median_duration_normalized_episode_count": bool(
                median_full_episodes >= requirements["required_full_episodes"]
                and median_recent_episodes
                >= requirements["required_recent10_episodes"]
            ),
            "gate_median_temporal_breadth": median_temporal[
                "temporal_breadth_pass"
            ],
            "gate_median_recent_tail_coverage": bool(
                0.05 <= median_recent_ratio <= 0.30
            ),
            "gate_median_local_boundary_sample": bool(
                int(median_econ["local_months"]) >= 8
            ),
            "gate_mean_broad_factor_evidence": bool(
                mean_econ["active_share_median_ge_1"] >= 0.90
            ),
            "gate_joint_state_confirmation": bool(
                full_jaccard >= 0.70
                and recent_jaccard >= 0.70
                and recent_difference <= 0.10
            ),
        }
        mean_core = all(
            gates[name]
            for name in (
                "gate_mean_duration_normalized_episode_count",
                "gate_mean_temporal_breadth",
                "gate_mean_recent_tail_coverage",
                "gate_mean_local_boundary_sample",
                "gate_mean_broad_factor_evidence",
            )
        )
        median_core = all(
            gates[name]
            for name in (
                "gate_median_duration_normalized_episode_count",
                "gate_median_temporal_breadth",
                "gate_median_recent_tail_coverage",
                "gate_median_local_boundary_sample",
            )
        )
        rows.append(
            {
                "threshold": float(threshold),
                "required_full_episodes": requirements[
                    "required_full_episodes"
                ],
                "required_recent10_episodes": requirements[
                    "required_recent10_episodes"
                ],
                "mean_full_monthly_activation": float(mean_active.mean()),
                "median_full_monthly_activation": float(median_active.mean()),
                "mean_recent10_monthly_activation": mean_recent_ratio,
                "median_recent10_monthly_activation": median_recent_ratio,
                "recent10_activation_abs_diff": recent_difference,
                "mean_full_monthly_episodes": mean_full_episodes,
                "median_full_monthly_episodes": median_full_episodes,
                "mean_recent10_monthly_episodes": mean_recent_episodes,
                "median_recent10_monthly_episodes": median_recent_episodes,
                "mean_early_episode_starts": mean_temporal[
                    "early_episode_starts"
                ],
                "mean_late_episode_starts": mean_temporal[
                    "late_episode_starts"
                ],
                "median_early_episode_starts": median_temporal[
                    "early_episode_starts"
                ],
                "median_late_episode_starts": median_temporal[
                    "late_episode_starts"
                ],
                "mean_full_longest_active_months": mean_longest,
                "median_full_longest_active_months": median_longest,
                "mean_local_months": int(mean_econ["local_months"]),
                "median_local_months": int(median_econ["local_months"]),
                "mean_broad_factor_share": float(
                    mean_econ["active_share_median_ge_1"]
                ),
                "full_monthly_mean_median_jaccard": full_jaccard,
                "recent10_monthly_mean_median_jaccard": recent_jaccard,
                "current_mean_active": bool(
                    monthly["unbounded_mean_knot"].iloc[-1] >= threshold
                ),
                "current_median_active": bool(
                    monthly["unbounded_median_knot"].iloc[-1] >= threshold
                ),
                "mean_core_pass": bool(mean_core),
                "median_core_pass": bool(median_core),
                **gates,
                "all_individual_gates_pass": bool(all(gates.values())),
            }
        )
    selected, decision = ic_v6.select_platform(pd.DataFrame(rows))
    return selected, decision, pd.DataFrame(temporal_rows)


def summarize_decision(base: dict[str, Any]) -> dict[str, Any]:
    if base["platform_found"]:
        points = int(base["selected_band_points"])
        label = "wide_stable" if points >= 5 else "narrow_stable"
        decision = "duration_normalized_transfer_supported_secondary"
    else:
        label = "reject"
        decision = "duration_normalized_transfer_not_supported"
    return {
        **base,
        "decision": decision,
        "stability_label": label,
        "triggered_by_v1_failure": True,
        "independent_sample_confirmation": False,
        "research_status": "SECONDARY_CONFIRMATION_RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }


def make_gate_delta(
    selection_v1: pd.DataFrame, selection_v2: pd.DataFrame
) -> pd.DataFrame:
    old = selection_v1.set_index("threshold")
    new = selection_v2.set_index("threshold")
    rows = []
    for threshold in ic_v6.THRESHOLDS:
        v1 = old.loc[threshold]
        v2 = new.loc[threshold]
        rows.append(
            {
                "threshold": float(threshold),
                "v1_mean_episode_gate": bool(v1["gate_mean_episode_count"]),
                "v2_mean_duration_episode_gate": bool(
                    v2["gate_mean_duration_normalized_episode_count"]
                ),
                "v2_mean_temporal_breadth_gate": bool(
                    v2["gate_mean_temporal_breadth"]
                ),
                "v1_median_episode_gate": bool(
                    v1["gate_median_episode_count"]
                ),
                "v2_median_duration_episode_gate": bool(
                    v2["gate_median_duration_normalized_episode_count"]
                ),
                "v2_median_temporal_breadth_gate": bool(
                    v2["gate_median_temporal_breadth"]
                ),
                "v1_mean_core_pass": bool(v1["mean_core_pass"]),
                "v2_mean_core_pass": bool(v2["mean_core_pass"]),
                "v1_median_core_pass": bool(v1["median_core_pass"]),
                "v2_median_core_pass": bool(v2["median_core_pass"]),
                "v1_joint_pass": bool(v1["all_individual_gates_pass"]),
                "v2_joint_pass": bool(v2["all_individual_gates_pass"]),
                "joint_pass_changed": bool(
                    v1["all_individual_gates_pass"]
                    != v2["all_individual_gates_pass"]
                ),
            }
        )
    return pd.DataFrame(rows)


def unchanged_gate_mismatches(
    selection_v1: pd.DataFrame, selection_v2: pd.DataFrame
) -> int:
    old = selection_v1.set_index("threshold")
    new = selection_v2.set_index("threshold")
    pairs = (
        ("gate_mean_recent_tail_coverage", "gate_mean_recent_tail_coverage"),
        ("gate_mean_local_boundary_sample", "gate_mean_local_boundary_sample"),
        ("gate_median_recent_tail_coverage", "gate_median_recent_tail_coverage"),
        ("gate_median_local_boundary_sample", "gate_median_local_boundary_sample"),
        ("gate_mean_broad_factor_evidence", "gate_mean_broad_factor_evidence"),
        ("gate_joint_state_confirmation", "gate_joint_state_confirmation"),
    )
    return sum(
        int((old[left].astype(bool) != new[right].astype(bool)).sum())
        for left, right in pairs
    )


def integrity_checks(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    selection_v1: pd.DataFrame,
    selection_v2: pd.DataFrame,
    temporal: pd.DataFrame,
    requirements: dict[str, Any],
    price_context_v1: pd.DataFrame,
    price_context_v2: pd.DataFrame,
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
) -> dict[str, Any]:
    price_columns = ["ann_return", "ann_vol", "sharpe_repo", "max_dd"]
    price_context_error = float(
        np.nanmax(
            np.abs(
                price_context_v1[price_columns].to_numpy(float)
                - price_context_v2[price_columns].to_numpy(float)
            )
        )
    )
    temporal_episode_sum_mismatches = int(
        (
            temporal["full_episode_starts"]
            != temporal["early_episode_starts"] + temporal["late_episode_starts"]
        ).sum()
    )
    checks = {
        "daily_rows": len(daily),
        "monthly_rows": len(monthly),
        "recent10_months": requirements["recent10_months"],
        "start": daily["date"].min().date().isoformat(),
        "end": daily["date"].max().date().isoformat(),
        "required_full_episodes": requirements["required_full_episodes"],
        "required_recent10_episodes": requirements[
            "required_recent10_episodes"
        ],
        "early_months": requirements["early_months"],
        "late_months": requirements["late_months"],
        "threshold_rows": len(selection_v2),
        "temporal_audit_rows": len(temporal),
        "candidate_count": int(scan_long["candidate"].nunique()),
        "scan_rows": len(scan_long),
        "wide_rows": len(scan_wide),
        "duration_formula_full_exact": bool(
            requirements["required_full_episodes"]
            == math.ceil(EXPECTED_MONTHLY_ROWS * 5 / 236)
        ),
        "duration_formula_recent_exact": bool(
            requirements["required_recent10_episodes"]
            == math.ceil(EXPECTED_RECENT10_MONTHS * 2 / 121)
        ),
        "temporal_episode_sum_mismatches": temporal_episode_sum_mismatches,
        "unchanged_gate_mismatches": unchanged_gate_mismatches(
            selection_v1, selection_v2
        ),
        "price_context_max_abs_error_vs_v1": price_context_error,
        "price_context_unique_max_per_window": int(
            scan_long.groupby("segment")[price_columns]
            .nunique(dropna=False)
            .max()
            .max()
        ),
        "strategy_outcomes_used_for_selection": False,
        "v1_failure_triggered_secondary_confirmation": True,
        "independent_sample_claimed": False,
    }
    checks["all_checks_passed"] = bool(
        checks["daily_rows"] == EXPECTED_DAILY_ROWS
        and checks["monthly_rows"] == EXPECTED_MONTHLY_ROWS
        and checks["recent10_months"] == EXPECTED_RECENT10_MONTHS
        and checks["start"] == "2015-10-19"
        and checks["end"] == "2026-08-17"
        and checks["required_full_episodes"] == 3
        and checks["required_recent10_episodes"] == 2
        and checks["early_months"] == 66
        and checks["late_months"] == 65
        and checks["threshold_rows"] == 31
        and checks["temporal_audit_rows"] == 62
        and checks["candidate_count"] == 62
        and checks["scan_rows"] == 310
        and checks["wide_rows"] == 62
        and checks["duration_formula_full_exact"]
        and checks["duration_formula_recent_exact"]
        and checks["temporal_episode_sum_mismatches"] == 0
        and checks["unchanged_gate_mismatches"] == 0
        and checks["price_context_max_abs_error_vs_v1"] <= 1e-15
        and checks["price_context_unique_max_per_window"] == 1
        and not checks["strategy_outcomes_used_for_selection"]
        and checks["v1_failure_triggered_secondary_confirmation"]
        and not checks["independent_sample_claimed"]
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"IM duration-normalized valuation integrity failed: {checks}")
    return checks


def build_record(
    selection: pd.DataFrame,
    decision: dict[str, Any],
    definition: pd.DataFrame,
    current: pd.DataFrame,
    factor_summary: pd.DataFrame,
) -> str:
    rule = definition.iloc[0]
    now = current.iloc[0]
    factor = factor_summary.iloc[0]
    if decision["platform_found"]:
        conclusion = (
            f"时长归一后形成{decision['selected_band_low']:.2f}—"
            f"{decision['selected_band_high']:.2f}共同平台，机械中心"
            f"{decision['design_center_threshold']:.2f}。"
        )
    else:
        conclusion = "时长归一后仍没有形成至少三点共同平台。"
    table_rows = []
    for row in selection.itertuples(index=False):
        table_rows.append(
            f"| {row.threshold:.2f} | {row.mean_recent10_monthly_activation:.2%} | "
            f"{row.median_recent10_monthly_activation:.2%} | "
            f"{int(row.mean_full_monthly_episodes)}/{int(row.median_full_monthly_episodes)} | "
            f"{int(row.mean_early_episode_starts)}/{int(row.mean_late_episode_starts)} | "
            f"{int(row.median_early_episode_starts)}/{int(row.median_late_episode_starts)} | "
            f"{'是' if row.all_individual_gates_pass else '否'} |"
        )
    return f"""# 中证1000固定经济估值时长归一确认 v2

## 结论

{conclusion}

- 决定：`{decision['decision']}`；稳定性：`{decision['stability_label']}`。
- 这是由v1失败触发的二次确认，不是独立样本；没有读取IM、MO或任何策略收益。

## 机械门槛

- IC全样本236个月/至少5段，换算到IM 131个月为至少{int(rule.im_required_full_episodes)}段；
- IC最近10年121个月/至少2段，换算到IM最近10年121个月仍为至少{int(rule.im_required_recent10_episodes)}段；
- 时间广度：前66个月（{rule.early_start}—{rule.early_end}）和后65个月（{rule.late_start}—{rule.late_end}）各至少出现一次全序列启动。

## 阈值结构

| 阈值 | 均值近10年覆盖 | 中位数近10年覆盖 | 全样本段数均值/中位数 | 均值前/后启动 | 中位数前/后启动 | 共同通过 |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: |
{chr(10).join(table_rows)}

## 因子与当前状态

- 月末样本{int(factor.monthly_rows)}个；PC1解释率{factor.pc1_explained_ratio:.2%}，有效维数{factor.effective_dimension:.2f}，等权均值与PC1排序Spearman为{factor.equal_mean_pc1_spearman:.4f}；
- {now.date}：PB {now.pb_aggregate:.2f}、ERP {now.erp:.2%}、过去一年股息贡献 {now.trailing_dividend_contribution:.2%}；
- 无界均值{now.unbounded_mean_knot:.3f}、二取三{now.unbounded_median_knot:.3f}；研究中心{now.design_center_threshold}，均值/中位数当前启动为{bool(now.mean_design_center_active)}/{bool(now.median_design_center_active)}。

## 边界

- 样本2015-10-19—2026-08-17；中证1000价格指数背景；
- 收益、波动和回撤字段只是同窗指数背景，不是估值策略收益；
- 无交易、成本、保证金、Put、网格或Call；研究观察，未批准实盘。
"""


def update_scan_artifacts(
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    record: str,
    decision: dict[str, Any],
    input_hashes: dict[str, str],
    git_before: str,
) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    scan_record = f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `{SCAN.name}`
- Run date: 2026-08-18
- Timezone: Asia/Shanghai
- Project: 中证1000固定估值本体
- Version: `{VERSION}`
- Subsystem: valuation_body
- Parameter group: duration-normalized episode gate; threshold 1.50—3.00 unchanged
- Scan type: candidate_bundle / outcome-free secondary confirmation
- Target entrypoint: `im_fixed_valuation_duration_normalized_v2.py`
- Working tree before: `{git_before}`
- Working tree after: recorded by finalizer

## Research Question

- Baseline: v1 fixed absolute economic scores and unchanged structural gates.
- Candidate grid: two families × 31 unchanged thresholds × five windows.
- Decision target: supported secondary confirmation or reject.
- Source-change rule: research_only_no_source_change.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- Promotion threshold: at least three adjacent joint pass points plus early/late event breadth.
- Rerun trigger: any frozen hash, sample length, gate parity or price-context parity failure.

## Implementation Anchor

- Official upstream: frozen `outputs/im_fixed_valuation_unbounded_transfer_v1/`.
- Score formulas and candidate grid are byte-identical frozen inputs; only the preregistered event-count gate changes.
- Existing IC v6 selection and metric helpers are reused.

## Data Snapshot

- Raw and metric sample: 2015-10-19—2026-08-17; 2,634 daily rows and 131 month ends.
- Sources: frozen v1 Legulegu/CSI/ChinaBond-derived artifacts.
- Cache write risk: none; no refresh or network download.
- Price mode: official CSI1000 price index; Asia/Shanghai calendar.

## Cost and Execution Assumptions

- No trades, fills, commission, slippage, leverage, financing, Put, grid or Call.
- Return fields are underlying-price-index context only.

## Runtime Override Plan

- No runtime override and no production default.
- Default v1 gate and v2 gate are compared in the same frozen sample.
- Formula, unchanged-gate and price-context parity are mandatory.

## Commands

```powershell
python im_fixed_valuation_duration_normalized_v2.py
python -m pytest -q test_im_fixed_valuation_duration_normalized_v2.py
uvx ruff check im_fixed_valuation_duration_normalized_v2.py test_im_fixed_valuation_duration_normalized_v2.py
```

## Output Files

- `record.md`, `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`.
- Formal audit tables are in `outputs/{VERSION}/`.

## Full-Sample Results

See `window_metrics.csv`; performance columns are underlying-index context only.

## Window Results

See `scan_summary.csv` for full/10Y/5Y/3Y/1Y.

## Stability Classification

- Label: `{decision['stability_label']}`.
- Platform: {decision['selected_band_low']}—{decision['selected_band_high']}.
- Data sensitivity: about 10.8 years; this is a v1-failure-triggered secondary confirmation.

## Decision

- Decision: `{decision['decision']}`.
- Recommended next action: user review before freezing an IM valuation line or starting Put/grid/Call.

## User-Facing Summary

{record}
"""
    (SCAN / "record.md").write_text(scan_record, encoding="utf-8")
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle_outcome_free_secondary_confirmation",
            "parameter_group": "duration_normalized_episode_gate",
            "baseline": {
                "definition": "v1 fixed economic units and fixed five-episode gate"
            },
            "candidate_grid": [
                {"family": family, "threshold": float(threshold)}
                for family in ic_v6.FAMILIES
                for threshold in ic_v6.THRESHOLDS
            ],
            "data_snapshot": {
                "start": "2015-10-19",
                "end": "2026-08-17",
                "daily_rows": EXPECTED_DAILY_ROWS,
                "monthly_rows": EXPECTED_MONTHLY_ROWS,
                "source": "frozen v1 local real-data artifacts",
                "cache_writes": "none",
            },
            "cost_model": {"applicable": False},
            "decision": decision["decision"],
            "stability_label": decision["stability_label"],
            "source_hashes": input_hashes,
            "warnings": [
                "secondary confirmation triggered by v1 failure; not independent sample",
                "return fields are underlying-index context only",
                "research only, not live approved",
            ],
        }
    )
    (SCAN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SCAN / "command_log.txt").write_text(
        "Working directory: D:\\动量策略\\新策略研究\n"
        "python im_fixed_valuation_duration_normalized_v2.py\n"
        "python -m pytest -q test_im_fixed_valuation_duration_normalized_v2.py\n"
        "uvx ruff check im_fixed_valuation_duration_normalized_v2.py "
        "test_im_fixed_valuation_duration_normalized_v2.py\n",
        encoding="utf-8",
    )


def main() -> None:
    input_hashes = verify_frozen_inputs(require_fresh_output=True)
    git_before = git_status()
    inputs = load_inputs()
    daily = inputs["daily"]
    monthly = inputs["monthly"]
    requirements = duration_requirements(monthly)
    definition = gate_definition(requirements)
    selection, base_decision, temporal = make_threshold_selection(
        monthly, inputs["economic"], requirements
    )
    decision = summarize_decision(base_decision)
    gate_delta = make_gate_delta(inputs["selection_v1"], selection)
    price_context = ic_v6.make_price_context(daily)
    scan_long, scan_wide = ic_v6.make_threshold_scan(
        daily, monthly, selection, price_context
    )
    current = ic_v6.make_current_state(
        daily, decision, inputs["threshold_map"]
    )
    checks = integrity_checks(
        daily,
        monthly,
        inputs["selection_v1"],
        selection,
        temporal,
        requirements,
        inputs["price_context_v1"],
        price_context,
        scan_long,
        scan_wide,
    )
    record = build_record(
        selection,
        decision,
        definition,
        current,
        inputs["factor_summary"],
    )

    STAGING.mkdir(parents=True)
    shutil.copy2(
        V1_OUTPUT / "daily_unbounded_fixed_scores.csv.gz",
        STAGING / "daily_unbounded_fixed_scores.csv.gz",
    )
    shutil.copy2(
        V1_OUTPUT / "monthly_unbounded_fixed_scores.csv",
        STAGING / "monthly_unbounded_fixed_scores.csv",
    )
    for name in (
        "economic_boundary.csv",
        "factor_structure_summary.csv",
        "price_index_context.csv",
        "raw_threshold_map.csv",
        "vintage_invariance.csv",
    ):
        shutil.copy2(V1_OUTPUT / name, STAGING / name)
    definition.to_csv(STAGING / "duration_gate_definition.csv", index=False)
    temporal.to_csv(STAGING / "temporal_episode_audit.csv", index=False)
    selection.to_csv(STAGING / "threshold_selection_v2.csv", index=False)
    gate_delta.to_csv(STAGING / "v1_v2_gate_delta.csv", index=False)
    scan_long.to_csv(STAGING / "scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_fixed_valuation_duration_normalized_v2.py\n"
        "python -m pytest -q test_im_fixed_valuation_duration_normalized_v2.py\n"
        "uvx ruff check im_fixed_valuation_duration_normalized_v2.py "
        "test_im_fixed_valuation_duration_normalized_v2.py\n",
        encoding="utf-8",
    )
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).astimezone().isoformat(),
        "spec_sha256": SPEC_HASH,
        "script_sha256": sha256(Path(__file__)),
        "input_hashes": input_hashes,
        "sample": {
            "start": "2015-10-19",
            "end": "2026-08-17",
            "daily_rows": len(daily),
            "monthly_rows": len(monthly),
        },
        "selection_uses_strategy_outcomes": False,
        "secondary_confirmation_triggered_by_v1_failure": True,
        "independent_sample_confirmation": False,
        "decision": decision,
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files = {
        path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(STAGING.iterdir())
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(
            {"version": VERSION, "spec_sha256": SPEC_HASH, "files": files},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    update_scan_artifacts(
        scan_long, scan_wide, record, decision, input_hashes, git_before
    )
    try:
        STAGING.rename(OUTPUT)
    except Exception:
        failed = (
            ROOT
            / "outputs"
            / f"{VERSION}_failed_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        )
        if STAGING.exists():
            shutil.move(str(STAGING), str(failed))
        raise
    print(json.dumps({"decision": decision, "integrity": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
