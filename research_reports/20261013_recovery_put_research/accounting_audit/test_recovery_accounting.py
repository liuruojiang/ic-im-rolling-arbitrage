"""Synthetic accounting/timing invariants; no historical performance claims."""
from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(ROOT),
               str(ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l2_account_ledger_repair_20260925")]
from trade_account import Account
from recovery_cycle_controller import RecoveryCycleController
import ic_im_v1_4_policy as policy


def controller(product="IM", mode="A"):
    market = SimpleNamespace(spot=lambda day: 100.0, quote=lambda *args, **kw: 100.0,
                             expiry_payoff=lambda day, contract: 0.0,
                             option_chain=lambda day: pd.DataFrame())
    formal = SimpleNamespace(v14_policy=policy, im_put_policy=SimpleNamespace(EFFECTIVE_DATE=date(2026, 1, 1)),
                             _third_friday=lambda year, month: date(year, month, 20))
    return RecoveryCycleController(product=product, arm="repeat_roll", mode=mode, market=market, formal=formal,
                                   selector=lambda *args: ("IC_PROTECTION", 2),
                                   roll_selector=lambda *args: {"tradable": False},
                                   candidate_decoder=lambda row: {"tradable": False},
                                   gov=pd.Series(dtype=float), spot=pd.Series(dtype=float), source=None,
                                   option_expiry=lambda product, contract: date(2027, 1, 20), option_fee=.0005)


def start(c, account, day=date(2026, 9, 30)):
    anchor = policy.default_extension(c.product)
    anchor.update(v14_route_state="short_put", v14_short_put_contract="MO2610-P-100",
                  v14_short_put_qty_normalized=1.5, v14_short_put_entry_premium=2.0,
                  v14_short_put_expiry=date(2026, 10, 16))
    c.pending = {"signal_date": "2026-09-29", "execution_date": str(day), "anchor": anchor,
                 "route_after_execution": "short_put", "action": "ENTER_SHORT_PUT",
                 "held_contract": None, "active_contract_after": "MO2610-P-100", "protection": None,
                 "decision_net_pnl": 0.0, "estimated_exit_protection_fee": 0.0}
    c.consume(day, account)


def test_option_premium_is_not_deducted_twice_and_all_fees_enter_cycle():
    c, account = controller(), Account(cash=100000.0)
    start(c, account)
    account.trade("2026-09-30", "open", "option|seller|MO2610-P-100", "option", -1.5, 2., 100., 3.)
    account.trade("2026-09-30", "open", "option|recovery_put|MO2612-P-100", "option", 1.5, 1., 100., 4.)
    account.accrue_cash("2026-09-30", 1000., .01)
    account.trade("2026-09-30", "open", "future|momentum|IM2612", "future", 1., 100., 200., 5.)
    c.observe(account)
    assert c.pnl == -7.0  # premium and liability/asset offset; only these fees.
    account.mark("2026-09-30", "option|seller|MO2610-P-100", 1.)
    account.mark("2026-09-30", "option|recovery_put|MO2612-P-100", 2.)
    c.observe(account)
    assert c.pnl == 293.0
    c.observe(account)
    assert c.pnl == 293.0
    assert sum(c.cycle["fees_by_sleeve"].values()) == 7.0


def test_cash_wait_has_no_phantom_index_profit():
    c, account = controller(), Account(cash=100000.0)
    start(c, account)
    c.anchor = policy.default_extension("IM")
    c.anchor.update(v14_route_state="cash_wait", v14_recovery_net_pnl=-10.0)
    c.pnl = -10.0
    risk = pd.Series(dict(valuation_tier=2, momentum_target_weight=0.0, momentum_120=-.1))
    target = pd.Series(dict(monthly_reset_due=False))
    c.market.spot = lambda day: 1000000.0
    c.close(date(2026, 10, 8), date(2026, 10, 9), account, risk, target, None)
    assert c.pnl == -10.0
    assert c.pending["action"] == "ENTER_RECOVERY_FUTURE"
    assert c.pending["decision_net_pnl"] == -10.0


@pytest.mark.parametrize("pnl,expected", [(7.0, "HOLD_RECOVERY"), (8.0, "EXIT_RECOVERY_AT_BREAKEVEN")])
def test_exit_needs_protection_fee_and_never_uses_next_open(pnl, expected):
    c, account = controller(), Account(cash=100000.0)
    start(c, account)
    c.anchor = policy.default_extension("IM")
    c.anchor.update(v14_route_state="recovery_future", v14_recovery_net_pnl=-10.0)
    account.trade("2026-09-30", "open", "option|recovery_put|MO2612-P-100", "option", 1.5, 1., 100., 0.)
    c.cursor = len(account.entries)
    c.pnl = pnl
    risk = pd.Series(dict(valuation_tier=2, momentum_target_weight=0.0, momentum_120=-.1))
    target = pd.Series(dict(monthly_reset_due=False))
    c.close(date(2026, 10, 8), date(2026, 10, 9), account, risk, target, None)
    assert c.pending["estimated_exit_protection_fee"] == 7.5
    assert c.pending["action"] == expected
    assert c.pending["execution_date"] == "2026-10-09"


def test_exit_open_gap_loss_is_retained_in_closed_cycle():
    c, account = controller(), Account(cash=100000.0)
    start(c, account)
    c.pnl = 8.0
    c.executing.update(action="EXIT_RECOVERY_AT_BREAKEVEN", decision_net_pnl=.5,
                       estimated_exit_protection_fee=7.5, signal_date="2026-10-08")
    account.trade("2026-10-08", "close", "option|recovery_put|MO2612-P-100", "option", 1.5, 1., 100., 0.)
    c.cursor = len(account.entries)
    account.trade("2026-10-09", "open", "option|recovery_put|MO2612-P-100", "option", -1.5, .9, 100., 7.5)
    c.after_open_trades(date(2026, 10, 9), account)
    assert c.records[0]["final_net_pnl"] == pytest.approx(-14.5)
    assert c.records[0]["negative_pnl_after_next_open"] is True
    assert c.active is False


def test_ic_assignment_protection_plan_precedes_next_open_and_has_no_3x():
    c = controller("IC", "C")
    risk = pd.Series(dict(core_put_target_delta=0.0))
    target = pd.Series(dict(core_put_qty_target=0.0, core_put_contract_target=None,
                            monthly_reset_due=False))
    plan = c._protection_plan(date(2026, 9, 30), date(2026, 10, 8), "assigned_etf", risk, target)
    assert plan["signal_date"] == "2026-09-30"
    assert plan["execution_date"] == "2026-10-08"
    assert plan["target_absolute_delta"] == .125
    assert plan["three_x_enabled"] is False


def test_ic_B_protects_assignment_when_ordinary_target_is_positive():
    c = controller("IC", "B")
    risk = pd.Series(dict(core_put_target_delta=.25))
    target = pd.Series(dict(core_put_qty_target=2.0, core_put_contract_target="IC_ORDINARY",
                            monthly_reset_due=False))
    plan = c._protection_plan(date(2026, 9, 30), date(2026, 10, 8), "assigned_etf", risk, target)
    assert plan["quantity_normalized"] == 2.0
    assert plan["contract"] == "IC_PROTECTION"  # Fresh T-chain, never borrow core identity.


def test_ic_monthly_preview_does_not_refresh_a_second_time():
    c = controller("IC", "B")
    risk = pd.Series(dict(core_put_target_delta=.25))
    target = pd.Series(dict(core_put_qty_target=2.0, core_put_contract_target="IC_ORDINARY",
                            monthly_reset_due=True, monthly_reset_reference_day="2026-10-16"))
    c._protection_plan(date(2026, 10, 14), date(2026, 10, 15), "recovery_future", risk, target)
    preview = c._protection_plan(date(2026, 10, 15), date(2026, 10, 16), "recovery_future", risk, target)
    actual = c._protection_plan(date(2026, 10, 16), date(2026, 10, 19), "recovery_future", risk, target)
    assert preview["refresh"] is False
    assert actual["refresh"] is True


def test_im_minimum_uses_strategy_future_at_102_percent_and_restores_policy_date():
    c = controller("IM", "C")
    old = c.formal.im_put_policy.EFFECTIVE_DATE
    observed = []
    c.market.quote = lambda day, contract, kind: 1234.0
    def select(chain, day, reference):
        observed.append((day, reference, c.formal.im_put_policy.EFFECTIVE_DATE))
        return SimpleNamespace(instrument="MO2612-P-1250")
    c.formal.select_im_put_for_reset = select
    risk = pd.Series(dict(core_put_target_qty_normalized=0.0))
    target = pd.Series(dict(core_put_qty_target=0.0, core_put_contract_target=None,
                            monthly_reset_due=False, quarter_core_eod="IM2612"))
    p1 = c._protection_plan(date(2026, 9, 29), date(2026, 9, 30), "recovery_future", risk, target)
    p2 = c._protection_plan(date(2026, 9, 30), date(2026, 10, 8), "recovery_future", risk, target)
    assert p1["quantity_normalized"] == p2["quantity_normalized"] == 1.5
    assert len(observed) == 1  # No daily reselection/rebalance of a held floor.
    assert observed[0][1:] == (1234.0, date(1900, 1, 1))
    assert c.formal.im_put_policy.EFFECTIVE_DATE == old


def test_im_minimum_does_not_reduce_higher_ordinary_protection():
    c = controller("IM", "C")
    c.formal.select_im_put_for_reset = lambda *args: SimpleNamespace(instrument="MO2612-P-1250")
    risk = pd.Series(dict(core_put_target_qty_normalized=2.0))
    target = pd.Series(dict(core_put_qty_target=2.0, core_put_contract_target="MO2612-P-1200",
                            monthly_reset_due=False, quarter_core_eod="IM2612"))
    plan = c._protection_plan(date(2026, 9, 30), date(2026, 10, 8), "recovery_future", risk, target)
    assert plan["quantity_normalized"] == 2.0
    assert plan["contract"] == "MO2612-P-1250"


def test_held_insurance_identity_is_independent_from_ordinary_core_target_changes():
    c = controller("IC", "B")
    risk = pd.Series(dict(core_put_target_delta=.25))
    target = pd.Series(dict(core_put_qty_target=2.0, core_put_contract_target="IC_ORDINARY",
                            monthly_reset_due=False))
    p1 = c._protection_plan(date(2026, 9, 29), date(2026, 9, 30), "recovery_future", risk, target)
    target["core_put_contract_target"] = "UNRELATED_CORE_AFTER_3X"
    target["core_put_qty_target"] = 9.0
    p2 = c._protection_plan(date(2026, 9, 30), date(2026, 10, 8), "recovery_future", risk, target)
    assert p1["contract"] == p2["contract"] == "IC_PROTECTION"
    assert p1["quantity_normalized"] == p2["quantity_normalized"] == 2
    assert p2["refresh"] is False
    assert p2["selection_reason"] == "hold_independent_protection_identity"
