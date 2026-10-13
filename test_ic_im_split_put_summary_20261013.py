"""IC summary identities follow the official ordinary planner, not a shared leg."""
from copy import deepcopy
from datetime import date

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_v1_4_policy as policy
import run_ic_im_v1_4_github_digest as runner


CORE = "510500P2612M07500"
MOMENTUM = "510500P2612M07300"


def _render(signal):
    latest = {"verified_day": "2026-10-12", "sequence": 0, "digest": "synthetic",
              "signals": {"IC": signal, "IM": {}}}
    report = runner.render_stored_close_report(latest)
    section = report.split("## IC", 1)[1].split("## IM", 1)[0]
    summary = next(line for line in section.splitlines() if line.startswith("- 普通核心保护Put目标"))
    return report, summary


def _signal():
    return {
        "product": "IC", "market_date": date(2026, 10, 12),
        "next_trade_date": date(2026, 10, 13), "close_confirmed": True,
        "v14_route_state": "future", "v14_action": "HOLD",
        "v14_profit_pending": False, "option_monthly_reset_due": False,
        "v14_core_put_contract": CORE, "v14_core_put_security_id": "10012099",
        "v14_core_put_qty": 10, "put_target_core_qty": 10,
        "put_target_contract": MOMENTUM, "put_target_security_id": "10012002",
        "put_target_momentum_qty": 3, "put_target_total_qty": 13,
        "core_put_target_delta": .25, "momentum_put_target_delta": .10,
        "core_put_driver": "synthetic core", "momentum_put_driver": "synthetic momentum",
    }


def test_split_nonzero_core_and_momentum_summary_matches_real_planner():
    signal = _signal()
    anchor = policy.default_extension("IC")
    anchor.update(v14_core_put_contract=CORE, v14_core_put_security_id="10012099",
                  v14_core_put_qty=10, post_put_contract=CORE,
                  post_put_security_id="10012099", post_put_qty=10,
                  verified_core_put_qty=10, verified_momentum_put_qty=0)
    bot._v14_schedule_ordinary_put("IC", signal, anchor, None)
    plan = signal["v14_ordinary_put_pending"]
    assert plan["legs"]["core"]["new_contract"] == CORE
    assert plan["legs"]["core"]["new_qty"] == 10
    assert plan["legs"]["momentum"]["new_contract"] == MOMENTUM
    assert plan["legs"]["momentum"]["new_qty"] == 3
    original = deepcopy(signal)
    _, summary = _render(signal)
    assert f"{CORE}；数量：10" in summary
    assert MOMENTUM not in summary
    assert signal == original


def test_initial_core_opening_without_independent_identity_uses_planned_contract():
    signal = _signal()
    signal.update(v14_core_put_contract=None, v14_core_put_security_id=None,
                  v14_core_put_qty=0, put_target_contract=CORE,
                  put_target_momentum_qty=0, put_target_total_qty=10)
    _, summary = _render(signal)
    assert f"{CORE}；数量：10" in summary


def test_monthly_reset_summary_uses_new_target_not_old_current_identity():
    signal = _signal()
    new_core = "510500P2701M07100"
    signal.update(option_monthly_reset_due=True, v14_core_put_current_contract=CORE,
                  v14_core_put_contract=new_core, put_target_contract=new_core,
                  v14_core_put_qty=12, put_target_core_qty=12)
    _, summary = _render(signal)
    assert f"{new_core}；数量：12" in summary
    assert CORE not in summary


def test_pending_profit_reentry_keeps_ordinary_core_and_separate_unexecuted_plan():
    signal = _signal()
    replacement = "510500P2701M07200"
    signal.update(v14_profit_pending=True, v14_profit_reentry_status="scheduled_t_plus_1_open",
                  v14_profit_execution_day="2026-10-13", v14_profit_old_contract=CORE,
                  v14_profit_reentry_contract=replacement, v14_profit_reentry_qty=12)
    report, summary = _render(signal)
    assert f"{CORE}；数量：10" in summary
    assert replacement not in summary
    assert f"买预选 {replacement}，数量 12；尚非成交" in report


def test_confirmed_profit_reentry_uses_updated_independent_core():
    signal = _signal()
    replacement = "510500P2701M07200"
    signal.update(v14_core_put_contract=replacement, v14_core_put_qty=12, put_target_core_qty=12,
                  v14_profit_reentry_status="confirmed_open_research_price",
                  v14_profit_open_price_day="2026-10-12", v14_profit_open_old_contract=CORE,
                  v14_profit_open_executed_contract=replacement, v14_profit_open_executed_qty=12,
                  v14_profit_exit_open_price=.6, v14_profit_reentry_entry_premium=.2)
    report, summary = _render(signal)
    assert f"{replacement}；数量：12" in summary
    assert MOMENTUM not in summary
    assert "3倍兑现开盘纸面确认" in report


@pytest.mark.parametrize("qty", [0, 0.0])
def test_zero_core_target_does_not_borrow_positive_momentum_identity(qty):
    signal = _signal()
    signal.update(put_target_core_qty=qty)
    _, summary = _render(signal)
    assert "无（普通核心保护目标数量为0）" in summary
    assert CORE not in summary and MOMENTUM not in summary


def test_sparse_historical_report_keeps_unknown_quantity_unknown():
    _, summary = _render({"v14_core_put_contract": CORE})
    assert f"{CORE}；数量：N/A" in summary
