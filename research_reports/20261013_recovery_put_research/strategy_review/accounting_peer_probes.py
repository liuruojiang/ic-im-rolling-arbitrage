"""Independent synthetic invariants for the research-only controller.

These probes do not measure historical returns.  They deliberately use a small
controlled quote set to test cash/asset bookkeeping and T-known planning.
"""
from __future__ import annotations

from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
ROOT = RUN.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(RUN))
sys.path.insert(0, str(ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l2_account_ledger_repair_20260925"))
import poe_ic_im_mainline_v1_4_bot as formal
from recovery_cycle_controller import RecoveryCycleController
from trade_account import Account


class DatedMarket:
    def __init__(self, product="IC"):
        self.product = product
        self.calls = []

    def spot(self, day):
        self.calls.append(("spot", str(day)))
        return 5.0 if self.product == "IC" else 5000.0

    def quote(self, day, contract, kind):
        self.calls.append(("quote", str(day), contract, kind))
        return 5000.0 if kind == "future" else 100.0

    def expiry_payoff(self, day, contract):
        self.calls.append(("expiry", str(day), contract))
        return 1.0

    def option_chain(self, day):
        self.calls.append(("chain", str(day)))
        return pd.DataFrame([
            {"instrument": f"MO2504-P-{strike}", "lastprice": premium,
             "volume": 10, "position": 10}
            for strike, premium in ((4750, 30), (5000, 80), (5100, 150))
        ])


def controller(product="IC", mode="C"):
    market = DatedMarket(product)
    selections = []

    def select(product, day, market, risk, target, gov):
        selections.append(str(day))
        # Distinct from the deliberately stale ordinary-core identity below.
        return "510500P2504M04750", 8.0 * float(risk.core_put_target_delta)

    c = RecoveryCycleController(
        product=product, arm="repeat_roll", mode=mode, market=market,
        formal=formal, selector=select, roll_selector=lambda *a: None,
        candidate_decoder=lambda r: {"tradable": False}, gov=None, spot=None,
        source=None, option_expiry=lambda p, contract: date(2025, 4, 23),
        option_fee=.0005,
    )
    return c, market, selections


def target(*, quantity=0.0, monthly=False, reference="2025-01-17", product="IC"):
    return pd.Series({
        "core_put_qty_target": quantity,
        "core_put_contract_target": "510500P2503M04500" if product == "IC" else "MO2503-P-4750",
        "quarter_core_eod": "IC2503" if product == "IC" else "IM2503",
        "monthly_reset_due": monthly,
        "monthly_reset_reference_day": reference,
        "future_close": 5000.0,
    })


def risk(delta=.125, im_quantity=1.5):
    return pd.Series({
        "core_put_target_delta": delta,
        "core_put_target_qty_normalized": im_quantity,
        "valuation_tier": 0,
        "momentum_target_weight": 0.0,
        "momentum_120": .10,
    })


def active(c, account, route="recovery_future", pnl=0.0):
    c.active = True
    c.anchor["v14_route_state"] = route
    c.pnl = pnl
    c.cursor = len(account.entries)
    c.cycle = {
        "cycle_id": "synthetic-probe",
        "pnl_by_sleeve": dict.fromkeys(("seller", "assigned_etf", "recovery_core", "recovery_put"), 0.0),
        "fees_by_sleeve": dict.fromkeys(("seller", "assigned_etf", "recovery_core", "recovery_put"), 0.0),
    }


def check_b_ic_assignment():
    c, _, _ = controller(mode="B")
    p = c._protection_plan(date(2025, 1, 22), date(2025, 1, 23),
                           "assigned_etf", risk(), target(quantity=1))
    assert p is not None and p["quantity_normalized"] > 0
    return p


def check_fresh_identity():
    c, _, selections = controller(mode="B")
    p = c._protection_plan(date(2025, 1, 22), date(2025, 1, 23),
                           "recovery_future", risk(), target(quantity=1))
    assert selections == ["2025-01-22"], selections
    assert p["contract"] == "510500P2504M04750", p
    return p


def check_single_monthly_event():
    c, _, selections = controller(mode="C")
    c._protection_plan(date(2025, 1, 15), date(2025, 1, 16),
                       "recovery_future", risk(delta=0), target(monthly=False))
    c._protection_plan(date(2025, 1, 16), date(2025, 1, 17),
                       "recovery_future", risk(delta=0), target(monthly=True))
    c._protection_plan(date(2025, 1, 17), date(2025, 1, 20),
                       "recovery_future", risk(delta=0), target(monthly=True))
    assert selections == ["2025-01-15", "2025-01-17"], selections
    return {"fresh_selection_dates": selections}


def check_im_current_102_and_t_only():
    c, market, _ = controller(product="IM", mode="C")
    t = target(quantity=0, product="IM")
    t["core_put_contract_target"] = None
    p = c._protection_plan(date(2025, 1, 20), date(2025, 1, 21),
                           "recovery_future", risk(delta=0, im_quantity=0), t)
    assert p["contract"] == "MO2504-P-5100", p
    assert p["quantity_normalized"] == 1.5, p
    assert all(call[1] == "2025-01-20" for call in market.calls), market.calls
    assert formal.im_put_policy.EFFECTIVE_DATE == date(2026, 9, 8)
    return {"plan": p, "market_accesses": market.calls}


def check_premium_asset_bookkeeping():
    c, _, _ = controller(product="IM")
    account = Account(cash=1000000)
    active(c, account)
    symbol = "option|recovery_put|MO2504-P-5100"
    account.trade("2025-01-21", "open", symbol, "option", 2, 10, 100, 5)
    c.observe(account)
    assert account.cash == 997995 and account.equity() == 999995
    assert c.pnl == -5
    account.mark("2025-01-21", symbol, 12)
    c.observe(account)
    assert c.pnl == 395
    account.trade("2025-01-22", "open", symbol, "option", -2, 11, 100, 5)
    c.observe(account)
    assert c.pnl == 190 and account.equity() == 1000190
    assert c.cycle["fees_by_sleeve"]["recovery_put"] == 10
    return {"final_net_pnl": c.pnl, "fees": 10, "final_cash": account.cash}


def check_ic_assignment_no_duplicate_loss():
    c, _, _ = controller(product="IC")
    account = Account(cash=1000000)
    active(c, account, route="short_put")
    s = "option|seller|510500P2501M10000"
    account.trade("2025-01-20", "open", s, "option", -2, 1, 10000, 5)
    account.assign_short_put("2025-01-22", s, "etf|assigned|510500", 10, 8)
    c.observe(account)
    assert c.pnl == -20005
    account.mark("2025-01-23", "etf|assigned|510500", 7.5)
    c.observe(account)
    assert c.pnl == -30005
    assert c.cycle["pnl_by_sleeve"]["assigned_etf"] == -10000
    return {"final_net_pnl": c.pnl, "by_sleeve": c.cycle["pnl_by_sleeve"]}


def check_excluded_sleeves_and_cashwait():
    c, _, _ = controller(product="IM", mode="A")
    account = Account(cash=1000000)
    active(c, account, route="cash_wait", pnl=-100)
    account.trade("2025-01-20", "open", "future|momentum|IM2503", "future", 1, 5000, 200, 10)
    account.mark("2025-01-20", "future|momentum|IM2503", 5100)
    account.accrue_cash("2025-01-20", 10000, .01)
    c.observe(account)
    assert c.pnl == -100
    assert c._protection_plan(date(2025, 1, 20), date(2025, 1, 21),
                              "cash_wait", risk(), target(product="IM")) is None
    return {"cycle_pnl": c.pnl, "account_equity": account.equity()}


def check_exit_fee_gate_public_close():
    c, _, _ = controller(product="IM", mode="C")
    account = Account(cash=1000000)
    account.trade("2025-01-20", "open", "option|recovery_put|MO2504-P-5100",
                  "option", 2, 10, 100, 5)
    active(c, account, pnl=499)
    # 2 * 100 multiplier * 5000 underlying * 5 bp = 500 expected sale fee.
    c.close(date(2025, 1, 20), date(2025, 1, 21), account,
            risk(im_quantity=1.5), target(quantity=1.5, product="IM"), None)
    assert c.pending["action"] == "HOLD_RECOVERY", c.pending
    assert c.pending["decision_net_pnl"] == -1
    c.pnl = 501
    c.close(date(2025, 1, 20), date(2025, 1, 21), account,
            risk(im_quantity=1.5), target(quantity=1.5, product="IM"), None)
    assert c.pending["action"] == "EXIT_RECOVERY_AT_BREAKEVEN", c.pending
    assert c.pending["protection"] is None
    return {k: c.pending[k] for k in ("action", "decision_net_pnl", "estimated_exit_protection_fee")}


def main():
    probes = [check_b_ic_assignment, check_fresh_identity, check_single_monthly_event,
              check_im_current_102_and_t_only, check_premium_asset_bookkeeping,
              check_ic_assignment_no_duplicate_loss, check_excluded_sleeves_and_cashwait,
              check_exit_fee_gate_public_close]
    output = {
        "scope": "synthetic invariants on actual controller and Account; not historical returns or complete replay",
        "hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   [RUN / "recovery_account.py", RUN / "recovery_cycle_controller.py", RUN / "preregistered_spec.txt"]},
        "probes": [],
    }
    for probe in probes:
        try:
            evidence = probe()
            output["probes"].append({"name": probe.__name__, "status": "PASS", "evidence": evidence})
        except Exception as exc:
            output["probes"].append({"name": probe.__name__, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    output["status"] = "PASS" if all(p["status"] == "PASS" for p in output["probes"]) else "FAIL"
    destination = HERE / "accounting_peer_probe_results.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
