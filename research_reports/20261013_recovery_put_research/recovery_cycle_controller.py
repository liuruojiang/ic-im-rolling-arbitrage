"""Research-only endogenous seller/recovery controller using account events.

No production state, network or filesystem writes.  All prices and targets are
supplied by the existing dated native account replay.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


class RecoveryCycleController:
    def __init__(self, *, product, arm, mode, market, formal, selector,
                 roll_selector, candidate_decoder, gov, spot, source,
                 option_expiry, option_fee, ic_min_delta=.125, im_min_qty=1.5):
        if mode not in {"A", "B", "C"}:
            raise ValueError(mode)
        self.product, self.arm, self.mode = product, arm, mode
        self.market, self.formal, self.selector = market, formal, selector
        self.roll_selector, self.candidate_decoder = roll_selector, candidate_decoder
        self.gov, self.spot, self.source = gov, spot, source
        self.option_expiry, self.option_fee = option_expiry, float(option_fee)
        self.ic_min_delta, self.im_min_qty = float(ic_min_delta), float(im_min_qty)
        self.anchor = formal.v14_policy.default_extension(product)
        self.pending = None
        self.executing = None
        self.active = False
        self.cycle_number = 0
        self.cycle = None
        self.pnl = 0.0
        self.cursor = 0
        self.records = []
        self.decisions = []
        self.plans = []
        self.attribution = []
        self.protection_selection = None

    @property
    def route(self):
        return self.anchor["v14_route_state"]

    def _category(self, event):
        name = str(event.get("symbol", ""))
        if name.startswith("option|seller|"):
            return "seller"
        if name.startswith("etf|assigned|"):
            return "assigned_etf"
        if name.startswith("future|core|"):
            return "recovery_core"
        if name.startswith("option|recovery_put|"):
            return "recovery_put"
        return None

    def observe(self, account):
        """MTM and fee equity changes, never option premium twice or cash interest."""
        end = len(account.entries)
        if self.active:
            for index in range(self.cursor, end):
                entry = account.entries[index]
                category = self._category(entry)
                if category is None:
                    continue
                pnl = float(entry.get("equity_change", 0.0) or 0.0)
                fee = float(entry.get("fee", 0.0) or 0.0)
                if not math.isfinite(pnl) or not math.isfinite(fee) or fee < 0:
                    raise RuntimeError("invalid cycle accounting event")
                self.pnl += pnl
                self.cycle["pnl_by_sleeve"][category] += pnl
                self.cycle["fees_by_sleeve"][category] += fee
                self.attribution.append({"cycle_id": self.cycle["cycle_id"], "entry_index": index,
                                         "date": entry["date"], "category": category,
                                         "event": entry["event"], "equity_change": pnl, "fee": fee,
                                         "symbol": entry.get("symbol"),
                                         "price_basis": entry.get("price_basis")})
        self.cursor = end

    def consume(self, day, account):
        if self.pending is None or self.pending["execution_date"] != day.isoformat():
            raise RuntimeError("endogenous route lacks exact prior-close execution plan")
        plan = self.pending
        self.pending = None
        self.anchor = deepcopy(plan["anchor"])
        self.executing = plan
        if plan["action"] == "ENTER_SHORT_PUT":
            if self.active:
                raise RuntimeError("new seller cycle overlaps existing account cycle")
            self.active = True
            self.cycle_number += 1
            self.pnl = 0.0
            self.cursor = len(account.entries)
            self.cycle = {"cycle_id": f"{self.product}-{self.cycle_number}-{day}",
                          "start_signal_date": plan["signal_date"], "start_execution_date": day.isoformat(),
                          "recovery_first_execution_date": None, "assigned_first_execution_date": None,
                          "closed": False, "pnl_by_sleeve": dict.fromkeys(
                              ("seller", "assigned_etf", "recovery_core", "recovery_put"), 0.0),
                          "fees_by_sleeve": dict.fromkeys(
                              ("seller", "assigned_etf", "recovery_core", "recovery_put"), 0.0)}
        if self.active and self.route == "assigned_etf" and self.cycle["assigned_first_execution_date"] is None:
            self.cycle["assigned_first_execution_date"] = day.isoformat()
        if self.active and self.route == "recovery_future" and self.cycle["recovery_first_execution_date"] is None:
            self.cycle["recovery_first_execution_date"] = day.isoformat()
        return SimpleNamespace(**{key: plan[key] for key in
                                   ("execution_date", "route_after_execution", "action",
                                    "active_contract_after", "held_contract")})

    def add_protection_target(self, target_positions, stable_quantity, scale, target_type):
        plan = self.executing["protection"] if self.executing else None
        if not plan or plan["quantity_normalized"] <= 0:
            return
        contract, normalized = plan["contract"], plan["quantity_normalized"]
        if plan["refresh"]:
            quantity = normalized * scale
            # Keep the native signature coherent for following unchanged days.
            stable_quantity("recovery_put", contract, normalized)
        else:
            quantity = stable_quantity("recovery_put", contract, normalized)
        target_positions[f"option|recovery_put|{contract}"] = target_type(
            "option", quantity, 10_000 if self.product == "IC" else 100,
            self.market.spot(date.fromisoformat(plan["signal_date"])))

    def after_open_trades(self, day, account):
        self.observe(account)
        if not self.executing or self.executing["action"] not in {
            "EXIT_RECOVERY_AT_BREAKEVEN", "SHORT_PUT_EXPIRE_WORTHLESS"
        }:
            return
        if not self.active:
            raise RuntimeError("cycle close without an active account cycle")
        if any(name.startswith(("option|recovery_put|", "option|seller|", "etf|assigned|"))
               for name in account.positions):
            raise RuntimeError("cycle close retains seller/assigned/protection position")
        self.cycle.update(closed=True, exit_signal_date=self.executing["signal_date"],
                          exit_execution_date=day.isoformat(), exit_action=self.executing["action"],
                          final_net_pnl=self.pnl, decision_net_pnl=self.executing["decision_net_pnl"],
                          estimated_exit_protection_fee=self.executing["estimated_exit_protection_fee"],
                          negative_pnl_after_next_open=self.pnl < 0)
        self.records.append(deepcopy(self.cycle))
        self.active = False
        self.cycle = None
        self.protection_selection = None

    def _exit_protection_fee(self, account, day):
        return sum(abs(lot.quantity) * lot.multiplier * self.market.spot(day) * self.option_fee
                   for name, lot in account.positions.items() if name.startswith("option|recovery_put|"))

    def _select_protection(self, day, risk, target, quantity):
        if self.product == "IC":
            return self.selector(self.product, day, self.market, risk, target, self.gov)
        # Buyer Put reference is the held/confirmed quarterly future, not the
        # index/IV reference used to select the 95% short seller candidate.
        reference = self.market.quote(day, str(target.quarter_core_eod), "future")
        original_expiry = self.formal._third_friday
        def actual_expiry(year, month):
            return date(2024, 2, 19) if (year, month) == (2024, 2) else original_expiry(year, month)
        with patch.object(self.formal.im_put_policy, "EFFECTIVE_DATE", date(1900, 1, 1)), \
             patch.object(self.formal, "_third_friday", side_effect=actual_expiry):
            selected = self.formal.select_im_put_for_reset(self.market.option_chain(day), day, reference)
        return str(selected.instrument), float(quantity)

    def _protection_plan(self, day, execution, route, risk, target):
        eligible = route == "recovery_future" or (
            self.product == "IC" and route == "assigned_etf")
        if self.mode == "A" or not eligible:
            self.protection_selection = None
            return None
        base_qty = float(target.core_put_qty_target)
        maintenance = bool(target.monthly_reset_due) and (
            self.product == "IM" or str(target.get("monthly_reset_reference_day", "")) == day.isoformat())
        desired_delta = float(risk.core_put_target_delta) if self.product == "IC" else None
        desired_qty = base_qty
        if self.mode == "C":
            if self.product == "IC":
                desired_delta = max(desired_delta, self.ic_min_delta)
            else:
                desired_qty = max(desired_qty, self.im_min_qty)
        metric = desired_delta if self.product == "IC" else desired_qty
        if not math.isfinite(metric) or metric < 0:
            raise RuntimeError("invalid recovery protection target scale")
        if metric <= 0:
            self.protection_selection = None
            return None
        held = self.protection_selection
        if held is None:
            reason = "first_entry_or_zero_to_positive"
        elif maintenance:
            reason = "actual_monthly_maintenance"
        elif held[2] != metric:
            reason = "target_scale_changed_fresh_selection"
        elif self.option_expiry(self.product, held[0]) <= execution:
            reason = "held_contract_expires_before_execution"
        else:
            reason = "hold_independent_protection_identity"
        refresh = reason != "hold_independent_protection_identity"
        if refresh:
            modified = risk.copy()
            if self.product == "IC":
                modified["core_put_target_delta"] = desired_delta
            else:
                modified["core_put_target_qty_normalized"] = desired_qty
            contract, quantity = self._select_protection(day, modified, target, desired_qty)
            self.protection_selection = (str(contract), float(quantity), metric)
        contract, quantity, _ = self.protection_selection
        if not isinstance(contract, str) or self.option_expiry(self.product, contract) <= execution:
            raise RuntimeError("recovery protection lacks unexpired T-known contract")
        if not math.isfinite(float(quantity)) or float(quantity) <= 0:
            raise RuntimeError("invalid recovery protection quantity")
        plan = {"signal_date": day.isoformat(), "execution_date": execution.isoformat(),
                "route_after_execution": route, "contract": contract,
                "quantity_normalized": float(quantity), "core_target_base_quantity": base_qty,
                "target_absolute_delta": desired_delta, "refresh": refresh,
                "selection_reason": reason,
                "selection_rule": "IC 95% ETF / IM 102% confirmed quarterly future; T+3 nearest listed maturity",
                "three_x_enabled": False}
        self.plans.append(deepcopy(plan))
        return plan

    def close(self, day, execution, account, risk, target, candidate_row):
        self.observe(account)
        if execution is None:
            return
        route_before = self.route
        held = self.anchor.get("v14_short_put_contract")
        candidate = self.candidate_decoder(candidate_row) if self.arm != "no_seller" else {"tradable": False}
        roll = None
        if route_before == "short_put":
            expiry = date.fromisoformat(str(self.anchor["v14_short_put_expiry"])[:10])
            if day < expiry:
                roll = self.roll_selector(self.source, self.product, day, execution, expiry,
                                          risk, target, self.gov, self.spot)
        exit_fee = self._exit_protection_fee(account, day) if route_before == "recovery_future" else 0.0
        decision_pnl = self.pnl - exit_fee
        signal = {"market_date": day, "next_trade_date": execution, "close_confirmed": True,
                  "valuation_tier": int(risk.valuation_tier), "valuation_puts_per_full_core": int(risk.valuation_tier),
                  "momentum_next_weight": float(risk.momentum_target_weight), "momentum_120": float(risk.momentum_120),
                  "option_monthly_reset_due": bool(target.monthly_reset_due),
                  "core_units_current": .5, "core_units_target": .5,
                  "total_units_current": .5, "total_units_target": .5,
                  "momentum_put_target_qty_normalized": 0.0}
        if route_before == "short_put":
            expiry = date.fromisoformat(str(self.anchor["v14_short_put_expiry"])[:10])
            if day >= expiry:
                settlement = self.market.expiry_payoff(day, held)
                signal.update(v14_model_settlement_confirmed=True,
                              v14_model_settlement_outcome="assigned" if settlement > 0 else "expired_worthless",
                              v14_recovery_net_pnl_mark=decision_pnl)
            else:
                signal["v14_short_put_mark"] = self.market.quote(day, held, "option")
        elif route_before in {"assigned_etf", "cash_wait", "recovery_future"}:
            if not self.active:
                raise RuntimeError("recovery route has no account cycle")
            signal["v14_recovery_net_pnl_mark"] = decision_pnl
            if route_before in {"assigned_etf", "cash_wait"}:
                signal["v14_model_recovery_future_confirmed"] = True
        policy = self.formal.v14_policy
        # This study applies the latest seller rules across each independent
        # historical panel; no dated production identity or ledger is changed.
        with patch.object(policy, "EFFECTIVE_SIGNAL_DATE", date(1900, 1, 1)), \
             patch.object(policy, "IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE", date(1900, 1, 1)), \
             patch.object(policy, "REPEAT_ROLL_EFFECTIVE_SIGNAL_DATE", date(1900, 1, 1)):
            result = policy.apply_policy(self.product, signal, self.anchor, candidate=candidate, roll_candidate=roll)
        new_anchor = {key: deepcopy(result[key]) for key in policy.default_extension(self.product)}
        protection = self._protection_plan(day, execution, new_anchor["v14_route_state"], risk, target)
        self.pending = {"signal_date": day.isoformat(), "execution_date": execution.isoformat(),
                        "route_before": route_before, "route_after_execution": new_anchor["v14_route_state"],
                        "action": result["v14_action"], "active_contract_after": new_anchor["v14_short_put_contract"],
                        "held_contract": held, "anchor": new_anchor, "protection": protection,
                        "decision_net_pnl": decision_pnl, "estimated_exit_protection_fee": exit_fee}
        self.decisions.append({key: deepcopy(value) for key, value in self.pending.items()
                               if key not in {"anchor", "protection"}})

    def snapshot(self, account):
        quantity = sum(lot.quantity for name, lot in account.positions.items()
                       if name.startswith("option|recovery_put|"))
        return {"seller_route": self.route, "recovery_cycle_active": self.active,
                "recovery_cycle_net_pnl": self.pnl if self.active else 0.0,
                "recovery_put_held_quantity": quantity}

    def audit(self):
        records = deepcopy(self.records)
        if self.active:
            current = deepcopy(self.cycle)
            current.update(closed=False, right_censored=True, final_net_pnl=self.pnl)
            records.append(current)
        return {"recovery_research_mode": self.mode, "recovery_cycle_records": records,
                "recovery_route_decisions": self.decisions, "recovery_protection_plans": self.plans,
                "recovery_cycle_attribution": self.attribution,
                "recovery_cycle_pnl_basis": "Account equity_change seller/assigned_etf/recovery_core/recovery_put; cash interest excluded",
                "recovery_exit_basis": "T close cycle net PnL less estimated protection sale fee; T+1 actual costs and gap retained",
                "recovery_protection_3x": False}
