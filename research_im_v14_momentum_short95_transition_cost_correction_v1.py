"""Rerun the IM momentum short-Put test with futures handoff costs restored."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import research_im_v13_momentum_short95_full_joint_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v14_momentum_short95_transition_cost_correction_r4"
SPEC = ROOT / "docs" / "im_v14_momentum_short95_transition_cost_correction_v1_spec.md"
ORIGINAL_COMPOSE_DUAL = prior.compose_dual


def corrected_compose_dual(*args, **kwargs):
    """Use the v1.3 composition, but charge futures turnover at route boundaries."""
    original = prior.FUTURES_ONE_WAY
    # Rebuild by temporarily replacing the function's source-level behavior is
    # deliberately avoided: copy its output and recompute only the omitted cost.
    result = ORIGINAL_COMPOSE_DUAL(*args, **kwargs)
    base, fixed_router, momentum_router, momentum_scale = args[:4]
    if momentum_router is None:
        return result
    normal_units = 0.5 * base.momentum_weight.astype(float)
    active = momentum_scale.reset_index(drop=True).astype(float).gt(0)
    held_normal = normal_units.where(~active, 0.0)
    charged = original * held_normal.diff().fillna(held_normal).abs()
    # v1.3 charged non-boundary normal turnover; add back only the omitted
    # entry/restore boundary component.
    prior_turnover = held_normal.diff().fillna(held_normal).abs()
    transitions = active.ne(active.shift(fill_value=False))
    omitted = original * prior_turnover.where(transitions, 0.0)
    result["futures_cost_rate"] = result.futures_cost_rate.astype(float) + omitted.to_numpy()
    result["ret"] = (
        (1 + result.futures_gross_ret + result.put_pnl_ret + result.call_pnl_ret)
        * (1 - result.futures_cost_rate) * (1 - result.put_cost_rate) * (1 - result.call_cost_rate)
        - 1 + result.cash_weight * prior.prior.CASH_DAILY
    )
    if result.ret.le(-1).any():
        raise RuntimeError("Invalid corrected returns")
    result["nav"] = (1 + result.ret).cumprod()
    result["drawdown"] = result.nav / result.nav.cummax() - 1
    return result


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=False)
    (RUN / "scan_meta.json").write_text(json.dumps({
        "run_id": RUN.name,
        "phase": "init",
        "project": "IC和IM滚动套利",
        "strategy": "IM v1.4 research correction",
        "entrypoint": Path(__file__).name,
        "source_change_rule": "research_only_no_production_change",
        "outputs": {},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prior.RUN = RUN
    prior.SPEC = SPEC
    prior.compose_dual = corrected_compose_dual
    prior.main()


if __name__ == "__main__":
    main()
