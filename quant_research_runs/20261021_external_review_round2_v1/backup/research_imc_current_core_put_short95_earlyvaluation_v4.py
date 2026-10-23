"""Adversarial v4: restore certified 2015 valuation before theoretical replay.

This wrapper keeps the v3 accounting/timing fixes and replaces only the two
places that consume valuation state: current core-Put sizing and short-Put
entry permission.  Missing early valuation is mapped to the previously
certified tier-3 reconstruction; any still-unknown date fails closed for short
Put entry and uses conservative tier 4 for protection sizing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as v3

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_coreput_highiv_short95_earlyvaluation_adversarial_v4"
EARLY = ROOT / "outputs" / "im_mo_2015_valuation_reconstruction_v13" / "early_valuation_reconstruction.csv"
EARLY_SCHEDULE = ROOT / "outputs" / "im_mo_2015_valuation_reconstruction_v13" / "extended_signal_schedules.csv.gz"


def effective_state() -> pd.DataFrame:
    state, _ = common.policy.load_authoritative_local_state()
    early = pd.read_csv(EARLY, parse_dates=["date"]).set_index("date")
    if not early.certified_tier.eq(3).all() or early.future_gov_row.any():
        raise RuntimeError("Certified early valuation invariant failed")
    state = state.copy()
    state["effective_valuation_tier"] = state.valuation_tier.where(
        state.valuation_score.notna(), state.date.map(early.certified_tier)
    )
    state["valuation_source"] = np.where(
        state.valuation_score.notna(), "current_valuation",
        np.where(state.effective_valuation_tier.notna(), "certified_early_tier3", "missing_failclosed"),
    )
    return state


def corrected_core_schedule(dates: pd.Series, scope: str, imc_mask: pd.Series | None = None) -> pd.DataFrame:
    state = effective_state().set_index("date").sort_index()
    if scope == "model":
        frozen = pd.read_csv(EARLY_SCHEDULE, parse_dates=["eval_date", "execution_date"])
        frozen = frozen[frozen.schedule_candidate.eq("reconstructed_valmom_floor3")].copy()
        frozen = frozen[frozen.execution_date.isin(pd.DatetimeIndex(dates))].sort_values("execution_date")
        evaluation = pd.DatetimeIndex(frozen.eval_date)
        execution = pd.DatetimeIndex(frozen.execution_date)
        if len(frozen) != len(dates) or execution[0] != pd.Timestamp(dates.iloc[0]):
            raise RuntimeError("Initial 2015-04-15 -> 2015-04-16 schedule is missing")
        valuation = pd.Series(
            np.where(
                frozen.reconstructed_certified_tier.to_numpy(dtype=float) > 0,
                frozen.reconstructed_certified_tier.to_numpy(dtype=float),
                frozen.valuation_tier.to_numpy(dtype=float),
            ),
            index=evaluation,
        )
        mom = pd.Series(frozen.momentum_120.to_numpy(dtype=float), index=evaluation)
    else:
        evaluation = pd.DatetimeIndex(dates.iloc[:-1])
        execution = pd.DatetimeIndex(dates.iloc[1:])
        valuation = state.reindex(evaluation).effective_valuation_tier
        mom = state.reindex(evaluation).momentum_120
    if mom.isna().any():
        raise RuntimeError("MOM120 does not cover replay dates")
    floor = common.mom_floor_state(mom)
    parent_target = np.maximum(valuation.fillna(4).to_numpy(dtype=float), np.where(floor, 3, 0))
    factor = 4 if scope == "model" else 8
    target = parent_target * factor
    if imc_mask is not None:
        allowed = imc_mask.reindex(execution, fill_value=False).to_numpy(dtype=bool)
        target = np.where(allowed, target, 0)
    return pd.DataFrame({
        "eval_date": evaluation,
        "execution_date": execution,
        "binary_target_qty": target.astype(int),
        "valuation_tier": valuation.fillna(4).to_numpy(dtype=int),
        "momentum_120": mom.to_numpy(dtype=float),
        "mom120_floor_active": floor,
        "put_buy_allowed": True,
    })


def corrected_prepare_signal(base: pd.DataFrame, options: pd.DataFrame) -> pd.DataFrame:
    result = ORIGINAL_PREPARE_SIGNAL(base, options)
    state = effective_state().set_index("date")
    aligned = state.reindex(pd.DatetimeIndex(result.eval_date))
    permission = (
        aligned.effective_valuation_tier.notna()
        & aligned.effective_valuation_tier.le(1)
        & aligned.momentum_120.notna()
        & aligned.momentum_120.ge(0)
    ).to_numpy(dtype=bool)
    result["short_put_permission"] = permission
    result["permission_reason"] = np.where(permission, "allowed", "valuation_or_mom120_failed")
    result["valuation_source"] = aligned.valuation_source.to_numpy()
    result["effective_valuation_tier"] = aligned.effective_valuation_tier.to_numpy()
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


ORIGINAL_PREPARE_SIGNAL = common.router.prepare_signal


def main() -> None:
    v3.RUN = RUN
    common.core_schedule = corrected_core_schedule
    common.router.prepare_signal = corrected_prepare_signal
    v3.main()

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scan_type"] = "adversarial_v4_with_certified_early_valuation"
    meta["early_valuation"] = {
        "path": str(EARLY),
        "sha256": sha(EARLY),
        "schedule_path": str(EARLY_SCHEDULE),
        "schedule_sha256": sha(EARLY_SCHEDULE),
        "rule": "missing valuation_score -> certified tier3; remaining unknown -> short entry fail closed and core protection tier4",
    }
    meta["source_hashes"]["v4_wrapper"] = sha(Path(__file__))
    meta["warnings"] = [
        "2015 early valuation is a later historical reconstruction, not a point-in-time archived 2015 feed.",
        *meta["warnings"],
    ]
    meta["decision"] = "research_only_pending_early_valuation_review"
    meta["stability_label"] = "early_valuation_restored_pending_review"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = RUN / "record.md"
    text = record.read_text(encoding="utf-8")
    text = text.replace(
        "# 高IV卖Put路由对抗复算：IMC基线parity修正版",
        "# 高IV卖Put路由对抗复算v4：恢复2015早期估值",
    )
    text = text.replace(
        "旧路由在首次高IV事件前未复现恒定1倍IMC基线，本版按同一收益会计修正，并强制无路由逐日/NAV误差不超过1e-12。",
        "本版保留IMC parity、因果开仓、下一交易日交割转IM和周期账本修复，并恢复既有2015早期估值认证：缺失score的244日为3档，未知失败关闭。",
    )
    record.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
