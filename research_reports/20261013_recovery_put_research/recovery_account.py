"""Research-only recovery-Put extension; A0 retains native v7 baseline behavior.

The accepted fix7 3x reset stays at T+1 open. Only ordinary core/momentum
buyer-Put adjustments switch from next close to next open; scheduled monthly
maintenance is modeled once on the futures settlement day's close. Production and frozen
historical account sources are not edited.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from native_fix4_historical_sources_v1 import HERE, ROOT, formal, sha256
from native_fix4_historical_sources_v2 import DatedSources
from native_fix4_risk_signals_v1 import OHLCV
from native_fix4_seller_lifecycle_v1 import roll_candidate

sys.path.insert(0, str(HERE.parent / "l2_account_ledger_repair_20260925"))
from trade_account import Account  # noqa: E402
from native_fix4_seller_lifecycle_v1 import candidate_from_row
from recovery_cycle_controller import RecoveryCycleController


FUTURE_FEE = 0.0001
OPTION_FEE = 0.0005
CASH_DAILY = 1.03 ** (1 / 252) - 1
INITIAL_FUTURE_NOTIONALS = 20.0
VERSION = "v1"
REENTRY_SELECTOR = None
MARGIN_MODEL = "v1"
EXECUTION_MODEL = "close_only"
LIFECYCLE_VERSION = "v1"
QUARTER_ROLL_AT_SIGNAL_CLOSE = False
PROFIT_RESTRIKE_MULTIPLE = 3.0
PROFIT_EXECUTION_PHASE = "close"
DISABLED_MODULES: frozenset[str] = frozenset()
BUYER_OPEN_SLEEVES: frozenset[str] = frozenset()
BUYER_OPEN_PROXY_MULTIPLIER = 1.0
CORE_UNITS = 0.5
CORE_BUYER_SCALE = 1.0
# Research-only opt-in: keep incremental core above the fixed 0.5 seller route.
# None preserves every prior replay verbatim.
FIXED_SELLER_CORE_UNITS: float | None = None
MOMENTUM_SCALE = 1.0
SELLER_SCALE = 1.0
SELLER_NAV_BUFFER_RATE = 0.15
MOMENTUM_SIGNAL_LAG_DAYS = 0
FUTURES_BASIS_RETENTION = 1.0
RECOVERY_RESEARCH_MODE = 'A0'
RECOVERY_IC_MIN_DELTA = .125
RECOVERY_IM_MIN_QTY = 1.5


@dataclass
class PositionTarget:
    kind: str
    quantity: float
    multiplier: float
    underlying: float


class Market:
    def __init__(self, source: DatedSources, product: str):
        self.source = source
        self.product = product
        self.options: dict[date, pd.DataFrame] = {}
        self.futures: dict[date, pd.DataFrame] = {}
        self.buyer_open_proxies: list[dict] = []
        self.future_index = pd.read_csv(OHLCV[product], parse_dates=["date"]).set_index("date")
        if product == "IM":
            index = pd.read_csv(OHLCV["IM"], parse_dates=["date"]).set_index("date")
            self.index = index.close
            self.index_open = index.open

    def option_chain(self, day: date) -> pd.DataFrame:
        if day not in self.options:
            self.options[day] = self.source.ic_on(day)[1] if self.product == "IC" else self.source.im_mo_on(day)
        return self.options[day]

    def future_chain(self, day: date) -> pd.DataFrame:
        if day not in self.futures:
            self.futures[day] = self.source.futures_on(self.product, day)
        return self.futures[day]

    def spot(self, day: date) -> float:
        return (self.source.ic_on(day)[0] if self.product == "IC"
                else float(self.index.loc[pd.Timestamp(day)]))

    def spot_open(self, day: date) -> float:
        if self.product == "IC":
            return float(self.source.ic_etf.loc[pd.Timestamp(day), "open"])
        return float(self.index_open.loc[pd.Timestamp(day)])

    def quote(self, day: date, contract: str, kind: str, *, phase: str = "close",
              prior_day: date | None = None) -> float:
        chain = self.future_chain(day) if kind == "future" else self.option_chain(day)
        name = "contract" if kind == "future" or self.product == "IC" else "instrument"
        price = ("open" if phase == "open" else "close") if kind == "future" else (
            ("open" if phase == "open" else "last") if self.product == "IC" else
            ("openprice" if phase == "open" else "lastprice"))
        found = chain.loc[chain[name].eq(contract), price]
        if len(found) != 1 or not math.isfinite(float(found.iloc[0])) or float(found.iloc[0]) <= 0:
            if phase == "open" and prior_day is not None:
                return self.quote(prior_day, contract, kind)
            raise RuntimeError(f"{self.product} dated {kind} model quote absent: {day} {contract} {phase}")
        observed = float(found.iloc[0])
        if kind == "future" and FUTURES_BASIS_RETENTION != 1.0:
            index_phase = "open" if phase == "open" else "close"
            index_price = float(self.future_index.loc[pd.Timestamp(day), index_phase])
            observed = index_price + FUTURES_BASIS_RETENTION * (observed - index_price)
            if not math.isfinite(observed) or observed <= 0:
                raise RuntimeError(f"invalid stressed futures quote: {self.product} {day} {contract}")
        return observed

    def buyer_open_quote(self, day: date, contract: str, signal_day: date) -> tuple[float, str]:
        try:
            return self.quote(day, contract, "option", phase="open"), "T_plus_1_dated_open_market_maker"
        except RuntimeError as missing_open:
            # No T+1 close/settlement is used here. Carry the T-close implied
            # volatility to the known T+1 underlying open and shorten maturity.
            if "model quote absent" not in str(missing_open):
                raise
            prior_price = self.quote(signal_day, contract, "option")
            strike = (float(contract.split("-")[-1]) if self.product == "IM"
                      else float(contract.split("M")[-1]) / 1000)
            expiry = option_expiry(self.product, contract)
            prior_spot = self.spot(signal_day)
            open_spot = self.spot_open(day)
            prior_tau = max((expiry - signal_day).days / 365, 1 / 365)
            open_tau = max((expiry - day).days / 365, 1 / 365)
            def put_price(underlying: float, tau: float, sigma: float) -> float:
                root_t = math.sqrt(tau)
                d1 = (math.log(underlying / strike) + (.03 + .5 * sigma * sigma) * tau) / (sigma * root_t)
                d2 = d1 - sigma * root_t
                cdf = lambda x: .5 * (1 + math.erf(x / math.sqrt(2)))
                return strike * math.exp(-.03 * tau) * cdf(-d2) - underlying * cdf(-d1)
            lower, upper = .001, 5.0
            if prior_price < put_price(prior_spot, prior_tau, lower) - 1e-8:
                raise RuntimeError(f"T-close option quote below model lower bound: {signal_day} {contract}")
            if prior_price > put_price(prior_spot, prior_tau, upper) + 1e-8:
                raise RuntimeError(f"T-close option quote above model upper bound: {signal_day} {contract}")
            for _ in range(64):
                midpoint = (lower + upper) / 2
                if put_price(prior_spot, prior_tau, midpoint) < prior_price:
                    lower = midpoint
                else:
                    upper = midpoint
            sigma = (lower + upper) / 2
            fair_open = put_price(open_spot, open_tau, sigma)
            proxy = fair_open * BUYER_OPEN_PROXY_MULTIPLIER
            if not math.isfinite(proxy) or proxy <= 0:
                raise RuntimeError(f"invalid T-known IV buyer open proxy: {day} {contract}")
            self.buyer_open_proxies.append({"date": day.isoformat(), "signal_date": signal_day.isoformat(),
                                            "contract": contract, "signal_close": prior_price,
                                            "signal_spot": prior_spot, "execution_spot_open": open_spot,
                                            "sticky_iv": sigma, "unshocked_proxy": fair_open,
                                            "proxy_multiplier": BUYER_OPEN_PROXY_MULTIPLIER,
                                            "fill": proxy})
            return proxy, "T_plus_1_open_T_close_sticky_IV_proxy"

    def expiry_payoff(self, day: date, contract: str) -> float:
        if self.product == "IM" and VERSION == "v7":
            found = self.option_chain(day).loc[lambda c: c.instrument.eq(contract), "settle"]
            if len(found) != 1 or not math.isfinite(float(found.iloc[0])) or float(found.iloc[0]) < 0:
                raise RuntimeError(f"official MO settlement missing: {day} {contract}")
            return float(found.iloc[0])
        return intrinsic(self.product, contract, self.spot(day))


def option_expiry(product: str, contract: str) -> date:
    if product == "IM":
        match = re.fullmatch(r"MO(\d{2})(\d{2})-[PC]-(\d+)", contract)
        if not match:
            raise RuntimeError(f"IM option identity invalid: {contract}")
        year, month = 2000 + int(match[1]), int(match[2])
        return date(2024, 2, 19) if (year, month) == (2024, 2) else formal._third_friday(year, month)
    match = re.fullmatch(r"510500[PC](\d{2})(\d{2})M(\d{5})", contract)
    if not match:
        raise RuntimeError(f"IC option identity invalid: {contract}")
    return formal._fourth_wednesday(2000 + int(match[1]), int(match[2]))


def intrinsic(product: str, contract: str, spot: float) -> float:
    if product == "IM":
        strike = float(contract.split("-")[-1])
        right = contract.split("-")[1]
    else:
        strike = float(contract.split("M")[-1]) / 1000
        right = "P" if "P" in contract else "C"
    return max(strike - spot, 0.0) if right == "P" else max(spot - strike, 0.0)


def symbol(kind: str, sleeve: str, contract: str) -> str:
    return f"{kind}|{sleeve}|{contract}"


def current_contract(name: str) -> str:
    return name.split("|", 2)[2]


def trade(account: Account, day: date, signal_day: date, name: str, target: PositionTarget,
          price: float, sequence: int, *, phase: str = "close", price_basis: str | None = None) -> None:
    current = account.positions[name].quantity if name in account.positions else 0.0
    change = target.quantity - current
    if abs(change) < 1e-7:
        return
    if target.kind == "future":
        fee = abs(change) * target.multiplier * price * FUTURE_FEE
    elif target.kind == "option":
        rate = .0001 if VERSION in {"v4", "v5", "v6", "v7"} and name.startswith("option|call|") else OPTION_FEE
        fee = abs(change) * target.multiplier * target.underlying * rate
    else:
        fee = abs(change) * price * FUTURE_FEE
    account.entries.append({"date": day.isoformat(), "event": "order_submitted", "symbol": name,
                            "signal_date": signal_day.isoformat(), "quantity_change": 0.0,
                            "cash_change": 0.0, "equity_change": 0.0, "order_id": f"{day}-{sequence}"})
    account.trade(day.isoformat(), phase, name, target.kind, change, price, target.multiplier, fee)
    account.entries[-1].update(signal_date=signal_day.isoformat(), model_fill_only=True,
                               price_basis=price_basis or f"T_plus_1_dated_{phase}_market_maker",
                               order_id=f"{day}-{sequence}")


def metrics(nav: np.ndarray) -> dict:
    if len(nav) < 2 or not np.isfinite(nav).all() or (nav <= 0).any():
        raise RuntimeError("invalid account NAV")
    peak = np.maximum.accumulate(np.r_[1.0, nav])[:-1]
    return {"final_nav": float(nav[-1]), "cagr": float(nav[-1] ** (252 / (len(nav) - 1)) - 1),
            "max_drawdown": float(np.min(nav / peak - 1))}


def replay(product: str, arm: str, source: DatedSources, lifecycle: pd.DataFrame,
           candidates: pd.DataFrame, gov: pd.Series, spot: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    risk = pd.read_csv(HERE / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz")
    targets = pd.read_csv(HERE / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz")
    lag_missing_contract_suppressed = 0
    lag_fresh_put_selections = 0
    if MOMENTUM_SIGNAL_LAG_DAYS:
        lag = MOMENTUM_SIGNAL_LAG_DAYS
        if lag < 0 or lag >= len(risk):
            raise RuntimeError("invalid momentum lag")
        risk["momentum_future_units"] = risk.momentum_future_units.shift(lag).fillna(
            float(risk.momentum_future_units.iloc[0]))
        targets["momentum_put_qty_target"] = targets.momentum_put_qty_target.shift(lag).fillna(
            float(targets.momentum_put_qty_target.iloc[0]))
        if product == "IM":
            held_momentum_contract = None
            lag_contracts = []
            for idx, row in enumerate(targets.itertuples(index=False)):
                signal_day = date.fromisoformat(str(row.signal_date))
                if float(targets.momentum_put_qty_target.iloc[idx]) <= 0:
                    held_momentum_contract = None
                elif (held_momentum_contract is None or bool(row.monthly_reset_due) or
                      option_expiry(product, held_momentum_contract) <= signal_day):
                    try:
                        selected = formal.select_independent_im_put_for_reset(
                            source.im_mo_on(signal_day), signal_day, float(row.future_close))
                        held_momentum_contract = str(selected.instrument)
                        lag_fresh_put_selections += 1
                    except (RuntimeError, ValueError, KeyError, IndexError):
                        held_momentum_contract = None
                        targets.at[idx, "momentum_put_qty_target"] = 0.0
                        lag_missing_contract_suppressed += 1
                lag_contracts.append(held_momentum_contract)
            targets["momentum_put_contract_target"] = lag_contracts
        else:
            valid_contract = [isinstance(contract, str) and
                              option_expiry(product, contract) > date.fromisoformat(str(signal_day))
                              for contract, signal_day in zip(targets.core_put_contract_target,
                                                              targets.signal_date)]
            suppressed = targets.momentum_put_qty_target.gt(0) & ~pd.Series(valid_contract)
            lag_missing_contract_suppressed = int(suppressed.sum())
            targets.loc[suppressed, "momentum_put_qty_target"] = 0.0
    life = lifecycle.loc[lifecycle["product"].eq(product) & lifecycle["arm"].eq(arm)].reset_index(drop=True)
    candidates = candidates.loc[candidates["product"].eq(product)].reset_index(drop=True)
    if not (len(risk) == len(targets) == len(life) + 1 == len(candidates) + 1):
        raise RuntimeError("account source calendar mismatch")
    market = Market(source, product)
    controller = None
    if RECOVERY_RESEARCH_MODE != 'A0':
        if EXECUTION_MODEL != 'mixed' or VERSION != 'v7' or CORE_UNITS != .5 or FIXED_SELLER_CORE_UNITS is not None:
            raise RuntimeError('recovery research requires the unchanged v7 mixed fixed-half-core configuration')
        controller = RecoveryCycleController(
            product=product, arm=arm, mode=RECOVERY_RESEARCH_MODE, market=market, formal=formal,
            selector=REENTRY_SELECTOR, roll_selector=roll_candidate, candidate_decoder=candidate_from_row,
            gov=gov, spot=spot, source=source, option_expiry=option_expiry, option_fee=OPTION_FEE,
            ic_min_delta=RECOVERY_IC_MIN_DELTA, im_min_qty=RECOVERY_IM_MIN_QTY)
    first_day = date.fromisoformat(risk.signal_date.iloc[0])
    first_ref = float(targets.future_close.iloc[0])
    initial_capital = INITIAL_FUTURE_NOTIONALS * first_ref * 200
    account = Account(cash=initial_capital)
    history = []
    core_entry = None
    core_override = None
    profit3x_trigger = None
    profit3x_preselected = None
    three_x_count = 0
    extra_entry = None
    extra_override = None
    extra_profit3x_trigger = None
    extra_profit3x_preselected = None
    extra_three_x_count = 0
    maintenance_events: list[dict] = []
    previous_target: dict[str, tuple[str, float]] = {}
    for ordinal, risk_row in enumerate(risk.itertuples(index=False)):
        day = date.fromisoformat(risk_row.signal_date)
        spot_price = market.spot(day)
        today_target = targets.iloc[ordinal]
        maintenance_today = bool(today_target.monthly_reset_due) and (
            product == "IM" or str(today_target.monthly_reset_reference_day) == day.isoformat())
        if maintenance_today:
            # A scheduled settlement-day maintenance supersedes a pending 3x
            # reset. The prior IC preview is not a second maintenance event.
            profit3x_trigger = None
            profit3x_preselected = None
            core_override = None
            extra_profit3x_trigger = None
            extra_profit3x_preselected = None
            extra_override = None
        sequence = 0
        if EXECUTION_MODEL == "mixed" and ordinal:
            prior_day = date.fromisoformat(risk.signal_date.iloc[ordinal - 1])
            for name, lot in list(account.positions.items()):
                if lot.kind == "etf":
                    opening = market.spot_open(day)
                else:
                    opening = market.quote(day, current_contract(name), lot.kind,
                                           phase="open", prior_day=prior_day)
                account.mark(day.isoformat(), name, opening)
        else:
            # Close-only v1-v3 diagnostics, preserved as immutable outputs.
            for name, lot in list(account.positions.items()):
                contract = current_contract(name)
                if lot.kind == "future":
                    account.mark(day.isoformat(), name, market.quote(day, contract, "future"))
                elif lot.kind == "option" and day >= option_expiry(product, contract):
                    payoff = market.expiry_payoff(day, contract)
                    if name.startswith("option|seller|") and product == "IC" and payoff > 0:
                        strike = float(contract.split("M")[-1]) / 1000
                        account.assign_short_put(day.isoformat(), name, "etf|assigned|510500", strike, spot_price)
                    else:
                        account.settle_cash_option(day.isoformat(), name, payoff)
                elif lot.kind == "option":
                    account.mark(day.isoformat(), name, market.quote(day, contract, "option"))
                elif lot.kind == "etf":
                    account.mark(day.isoformat(), name, spot_price)
        if ordinal:
            previous = ordinal - 1
            signal_day = date.fromisoformat(risk.signal_date.iloc[previous])
            r = risk.iloc[previous]
            t = targets.iloc[previous]
            l = (controller.consume(day, account) if controller is not None
                 else life.iloc[previous])
            if l.execution_date != day.isoformat():
                raise RuntimeError("account signal/execution date mismatch")
            route = l.route_after_execution
            if route not in {"future", "short_put", "assigned_etf", "cash_wait", "recovery_future"}:
                raise RuntimeError("invalid seller route for account")
            # The signal-date equity and future close are known before today's fill.
            scale = float(history[-1]["equity_close"]) / (float(t.future_close) * 200)
            if not math.isfinite(scale) or scale <= 0:
                raise RuntimeError("invalid decision-known sizing capital")
            future_contract = str(t.quarter_core_eod)
            if not future_contract:
                raise RuntimeError("missing native quarterly contract")
            target_positions: dict[str, PositionTarget] = {}
            target_signature: dict[str, tuple[str, float]] = {}
            split_core = product == "IC" and FIXED_SELLER_CORE_UNITS is not None
            fixed_units = float(FIXED_SELLER_CORE_UNITS) if split_core else CORE_UNITS
            extra_units = max(CORE_UNITS - fixed_units, 0.0) if split_core else 0.0
            if split_core and (fixed_units <= 0 or fixed_units > CORE_UNITS):
                raise RuntimeError("invalid fixed seller core split")
            def stable_quantity(sleeve: str, contract: str, normalized: float) -> float:
                name = symbol("future" if sleeve in {"core", "core_extra", "momentum", "grid"} else "option", sleeve, contract)
                target_signature[sleeve] = (contract, normalized)
                if (VERSION in {"v4", "v5", "v6", "v7"} and previous_target.get(sleeve) == (contract, normalized)
                        and name in account.positions):
                    return account.positions[name].quantity
                return normalized * scale
            if maintenance_today or route != "future":
                core_override = None
            if PROFIT_EXECUTION_PHASE == "open" and profit3x_trigger == day and route == "future":
                if profit3x_preselected is None:
                    raise RuntimeError("T-known 3x re-entry selection missing")
                core_override = (profit3x_preselected[0], profit3x_preselected[1] * (fixed_units / 0.5 if split_core else CORE_BUYER_SCALE))
            if split_core and PROFIT_EXECUTION_PHASE == "open" and extra_profit3x_trigger == day:
                if extra_profit3x_preselected is None:
                    raise RuntimeError("T-known extra-core 3x re-entry selection missing")
                extra_override = (extra_profit3x_preselected[0], extra_profit3x_preselected[1] * extra_units / 0.5)
            future_units = {"core": fixed_units if route in {"future", "recovery_future"} else 0.0,
                            "momentum": float(r.momentum_future_units) * MOMENTUM_SCALE, "grid": float(r.grid_target_units)}
            if split_core and extra_units > 0:
                future_units["core_extra"] = extra_units
            if "momentum" in DISABLED_MODULES:
                future_units["momentum"] = 0.0
            if "grid" in DISABLED_MODULES:
                future_units["grid"] = 0.0
            for sleeve, units in future_units.items():
                if units > 0:
                    target_positions[symbol("future", sleeve, future_contract)] = PositionTarget("future", stable_quantity(sleeve, future_contract, units), 200, float(t.future_close))
            buyer_multiplier = 10_000 if product == "IC" else 100
            if product == "IC":
                contract = t.core_put_contract_target if isinstance(t.core_put_contract_target, str) else None
                if core_override is not None:
                    contract = core_override[0]
                for sleeve, qty in (("core_put", float(t.core_put_qty_target) * (fixed_units / 0.5 if split_core else CORE_BUYER_SCALE) if route == "future" else 0.0),
                                    ("momentum_put", float(t.momentum_put_qty_target) * MOMENTUM_SCALE)):
                    if "buyer_put" in DISABLED_MODULES or ("momentum" in DISABLED_MODULES and sleeve == "momentum_put"):
                        qty = 0.0
                    if sleeve == "core_put" and core_override is not None:
                        qty = core_override[1]
                    if qty > 0 and contract and option_expiry(product, contract) > day:
                        target_positions[symbol("option", sleeve, contract)] = PositionTarget("option", stable_quantity(sleeve, contract, qty), buyer_multiplier, spot_price)
                if split_core and extra_units > 0 and "buyer_put" not in DISABLED_MODULES:
                    extra_contract = extra_override[0] if extra_override is not None else t.core_put_contract_target
                    extra_qty = (extra_override[1] if extra_override is not None else
                                 float(t.core_put_qty_target) * extra_units / 0.5)
                    if extra_qty > 0 and isinstance(extra_contract, str) and option_expiry(product, extra_contract) > day:
                        target_positions[symbol("option", "core_extra_put", extra_contract)] = PositionTarget(
                            "option", stable_quantity("core_extra_put", extra_contract, extra_qty), buyer_multiplier, spot_price)
            else:
                for sleeve, contract, qty in (("core_put", t.core_put_contract_target, float(t.core_put_qty_target) * CORE_BUYER_SCALE if route == "future" else 0.0),
                                              ("momentum_put", t.momentum_put_contract_target, float(t.momentum_put_qty_target) * MOMENTUM_SCALE)):
                    if "buyer_put" in DISABLED_MODULES or ("momentum" in DISABLED_MODULES and sleeve == "momentum_put"):
                        qty = 0.0
                    if sleeve == "core_put" and core_override is not None:
                        contract, qty = core_override
                    if qty > 0 and isinstance(contract, str) and option_expiry(product, contract) > day:
                        target_positions[symbol("option", sleeve, contract)] = PositionTarget("option", stable_quantity(sleeve, contract, qty), buyer_multiplier, spot_price)
                call_contract = t.call_contract_target
                call_qty = float(t.call_qty_normalized_target) if route == "future" else 0.0
                if "call" in DISABLED_MODULES:
                    call_qty = 0.0
                if call_qty < 0 and isinstance(call_contract, str) and option_expiry(product, call_contract) > day:
                    target_positions[symbol("option", "call", call_contract)] = PositionTarget("option", stable_quantity("call", call_contract, call_qty), 100, spot_price)
            if route == "short_put":
                contract = str(l.active_contract_after)
                if option_expiry(product, contract) > day:
                    previous_seller = next((account.positions[n] for n in account.positions if n.startswith("option|seller|") and current_contract(n) == contract), None)
                    if previous_seller is not None:
                        seller_qty = previous_seller.quantity
                    elif l.action == "ENTER_SHORT_PUT":
                        seller_qty = -float(candidates.iloc[previous].candidate_qty_normalized) * SELLER_SCALE * scale
                    elif l.action == "ROLL_SHORT_PUT":
                        expiry = option_expiry(product, str(l.held_contract))
                        replacement = roll_candidate(source, product, signal_day, day, expiry, r, t, gov, spot)
                        if replacement.get("contract") != contract:
                            raise RuntimeError("account roll candidate differs from native FSM")
                        seller_qty = -float(replacement["qty_normalized"]) * SELLER_SCALE * scale
                    else:
                        raise RuntimeError("seller active without prior lot or entry/roll")
                    target_positions[symbol("option", "seller", contract)] = PositionTarget("option", seller_qty, buyer_multiplier, spot_price)
            if route != "assigned_etf" and "etf|assigned|510500" in account.positions:
                # Physical delivery is sold only when the policy enters recovery future.
                pass
            elif route == "assigned_etf" and "etf|assigned|510500" in account.positions:
                target_positions["etf|assigned|510500"] = PositionTarget("etf", account.positions["etf|assigned|510500"].quantity, 1, spot_price)
            if controller is not None:
                controller.add_protection_target(target_positions, stable_quantity, scale, PositionTarget)
            names = sorted(set(account.positions) | set(target_positions))
            if maintenance_today:
                names = [name for name in names if not (
                    name.startswith("option|momentum_put|") or
                    (split_core and name.startswith("option|core_extra_put|")) or
                    (route == "future" and name.startswith("option|core_put|")))]
            sequence = 0
            if EXECUTION_MODEL == "mixed":
                open_names = set()
                profit3x_open_entry = None
                extra_profit3x_open_entry = None
                ordinary_core_open_entry = None
                ordinary_extra_open_entry = None
                buyer_open_sleeves = BUYER_OPEN_SLEEVES | (frozenset({"core_extra_put"}) if split_core else frozenset())
                for name in names:
                    desired = target_positions.get(name)
                    if name.startswith("option|recovery_put|"):
                        open_names.add(name)
                    elif name.startswith("future|") or name.startswith("option|seller|"):
                        open_names.add(name)
                    elif (PROFIT_EXECUTION_PHASE == "open" and profit3x_trigger == day
                          and route == "future" and name.startswith("option|core_put|")):
                        open_names.add(name)
                    elif (split_core and PROFIT_EXECUTION_PHASE == "open" and extra_profit3x_trigger == day
                          and name.startswith("option|core_extra_put|")):
                        open_names.add(name)
                    elif (not maintenance_today
                          and any(name.startswith(f"option|{sleeve}|") and
                                  (sleeve in {"momentum_put", "core_extra_put"} or route == "future")
                                  for sleeve in buyer_open_sleeves)):
                        open_names.add(name)
                    elif name.startswith(("option|core_put|", "option|call|")) and desired is None and route != "future":
                        open_names.add(name)
                    elif name.startswith("etf|") and desired is None:
                        open_names.add(name)
                for name in sorted(open_names, key=lambda n: (
                    0 if PROFIT_EXECUTION_PHASE == "open" and (
                        (profit3x_trigger == day and n.startswith("option|core_put|")) or
                        (split_core and extra_profit3x_trigger == day and n.startswith("option|core_extra_put|")))
                    and n not in target_positions else 1, n)):
                    if name.startswith("option|seller|") and option_expiry(product, current_contract(name)) <= day:
                        continue
                    desired = target_positions.get(name)
                    if desired is None:
                        lot = account.positions[name]
                        desired = PositionTarget(lot.kind, 0.0, lot.multiplier, market.spot_open(day))
                    elif desired.kind == "option":
                        desired = PositionTarget(desired.kind, desired.quantity, desired.multiplier, market.spot_open(day))
                    current = account.positions[name].quantity if name in account.positions else 0.0
                    if abs(desired.quantity - current) < 1e-7:
                        continue
                    contract = current_contract(name)
                    strict_profit_open = (PROFIT_EXECUTION_PHASE == "open" and (
                        (profit3x_trigger == day and name.startswith("option|core_put|")) or
                        (split_core and extra_profit3x_trigger == day and name.startswith("option|core_extra_put|"))))
                    strict_ordinary_open = (not maintenance_today
                                            and any(name.startswith(f"option|{sleeve}|") and
                                                    (sleeve in {"momentum_put", "core_extra_put"} or route == "future")
                                                    for sleeve in buyer_open_sleeves))
                    price_basis = None
                    if name.startswith("option|recovery_put|"):
                        price, price_basis = market.buyer_open_quote(day, contract, signal_day)
                    elif strict_ordinary_open and not strict_profit_open:
                        price, price_basis = market.buyer_open_quote(day, contract, signal_day)
                    else:
                        price = market.spot_open(day) if desired.kind == "etf" else market.quote(
                            day, contract, desired.kind, phase="open",
                            prior_day=None if strict_profit_open else signal_day)
                    sequence += 1
                    trade(account, day, signal_day, name, desired, price, sequence,
                          phase="open", price_basis=price_basis)
                    if strict_profit_open and desired.quantity > 0:
                        if name.startswith("option|core_extra_put|"):
                            extra_profit3x_open_entry = price
                        else:
                            profit3x_open_entry = price
                    elif strict_ordinary_open and name.startswith("option|core_put|") and desired.quantity > 0:
                        ordinary_core_open_entry = price
                    elif strict_ordinary_open and name.startswith("option|core_extra_put|") and desired.quantity > 0:
                        ordinary_extra_open_entry = price
                if controller is not None:
                    controller.after_open_trades(day, account)
                for name, lot in list(account.positions.items()):
                    contract = current_contract(name)
                    if lot.kind == "future":
                        account.mark(day.isoformat(), name, market.quote(day, contract, "future"))
                    elif lot.kind == "option" and day >= option_expiry(product, contract):
                        payoff = market.expiry_payoff(day, contract)
                        if name.startswith("option|seller|") and product == "IC" and payoff > 0:
                            strike = float(contract.split("M")[-1]) / 1000
                            account.assign_short_put(day.isoformat(), name, "etf|assigned|510500", strike, spot_price)
                        else:
                            account.settle_cash_option(day.isoformat(), name, payoff)
                    elif lot.kind == "option":
                        account.mark(day.isoformat(), name, market.quote(day, contract, "option"))
                    elif lot.kind == "etf":
                        account.mark(day.isoformat(), name, spot_price)
                close_names = [name for name in names if name not in open_names]
            else:
                close_names = names
            for name in close_names:
                desired = target_positions.get(name)
                if desired is None:
                    if name not in account.positions:
                        continue
                    lot = account.positions[name]
                    desired = PositionTarget(lot.kind, 0.0, lot.multiplier, spot_price)
                current = account.positions[name].quantity if name in account.positions else 0.0
                if abs(desired.quantity - current) < 1e-7:
                    continue
                contract = current_contract(name)
                price = spot_price if desired.kind == "etf" else market.quote(day, contract, desired.kind)
                sequence += 1
                trade(account, day, signal_day, name, desired, price, sequence)
            maintenance_core_entry = None
            maintenance_extra_entry = None
            maintenance_signatures: dict[str, tuple[str, float] | None] = {}
            if maintenance_today:
                # Today's formally selected target uses today's confirmed close.
                # This is the pre-existing special maintenance clock, not an
                # ordinary T signal pushed into tomorrow's open/close arm.
                close_scale = account.equity() / (float(today_target.future_close) * 200)
                if not math.isfinite(close_scale) or close_scale <= 0:
                    raise RuntimeError(f"invalid maintenance sizing capital: {day}")
                maintenance_sleeves = ("core_put", "momentum_put") + (("core_extra_put",) if split_core and extra_units > 0 else ())
                for sleeve in maintenance_sleeves:
                    target_sleeve = "core_put" if sleeve == "core_extra_put" else sleeve
                    normalized = float(today_target[f"{target_sleeve}_qty_target"])
                    if sleeve == "core_put":
                        normalized *= fixed_units / 0.5 if split_core else CORE_BUYER_SCALE
                    elif sleeve == "core_extra_put":
                        normalized *= extra_units / 0.5
                    else:
                        normalized *= MOMENTUM_SCALE
                    if sleeve == "core_put" and route != "future":
                        normalized = 0.0
                    if "buyer_put" in DISABLED_MODULES or (sleeve == "momentum_put" and "momentum" in DISABLED_MODULES):
                        normalized = 0.0
                    contract = (today_target.core_put_contract_target if product == "IC" else
                                today_target[f"{sleeve}_contract_target"])
                    if not isinstance(contract, str) or option_expiry(product, contract) <= day:
                        contract = None
                    if normalized > 0 and contract is None:
                        raise RuntimeError(f"maintenance target missing valid contract: {product} {day} {sleeve}")
                    desired_name = symbol("option", sleeve, contract) if normalized > 0 else None
                    held_names = [name for name in account.positions if name.startswith(f"option|{sleeve}|")]
                    sides = 0
                    for held_name in held_names:
                        if held_name == desired_name:
                            continue
                        lot = account.positions[held_name]
                        sequence += 1
                        trade(account, day, day, held_name,
                              PositionTarget("option", 0.0, lot.multiplier, spot_price),
                              market.quote(day, current_contract(held_name), "option"), sequence,
                              price_basis="T_monthly_maintenance_close")
                        sides += 1
                    if desired_name is not None:
                        desired_qty = normalized * close_scale
                        current_qty = account.positions[desired_name].quantity if desired_name in account.positions else 0.0
                        if abs(desired_qty - current_qty) >= 1e-7:
                            sequence += 1
                            trade(account, day, day, desired_name,
                                  PositionTarget("option", desired_qty, buyer_multiplier, spot_price),
                                  market.quote(day, contract, "option"), sequence,
                                  price_basis="T_monthly_maintenance_close")
                            sides += 1
                        maintenance_signatures[sleeve] = (contract, normalized)
                        if sleeve == "core_put":
                            maintenance_core_entry = market.quote(day, contract, "option")
                        elif sleeve == "core_extra_put":
                            maintenance_extra_entry = market.quote(day, contract, "option")
                    else:
                        maintenance_signatures[sleeve] = None
                    maintenance_events.append({"date": day.isoformat(), "sleeve": sleeve,
                                               "contract": contract, "normalized_qty": normalized,
                                               "trade_sides": sides})
            # 3x is a separate current-rule buyer reset, never a seller fill.
            if route == "future" and not maintenance_today:
                core = next((n for n in account.positions if n.startswith("option|core_put|")), None)
                if core is None:
                    core_entry = None
                elif core_entry is None or l.action in {"SHORT_PUT_EXPIRE_WORTHLESS", "EXIT_RECOVERY_AT_BREAKEVEN"}:
                    core_entry = ordinary_core_open_entry if ordinary_core_open_entry is not None else account.positions[core].mark
                if PROFIT_EXECUTION_PHASE == "open" and profit3x_trigger == day:
                    if core is None or profit3x_open_entry is None:
                        raise RuntimeError("3x open execution did not buy a protected core")
                    core_entry = profit3x_open_entry
                    three_x_count += 1
                if PROFIT_EXECUTION_PHASE == "close" and profit3x_trigger == day and core is not None:
                    # T trigger; T+1 confirmed-close model re-entry.
                    lot = account.positions[core]
                    price = lot.mark
                    held_qty = lot.quantity
                    sequence += 1
                    trade(account, day, signal_day, core, PositionTarget("option", 0, lot.multiplier, spot_price), price, sequence)
                    new_contract, new_qty = (current_contract(core), held_qty / scale) if REENTRY_SELECTOR is None else REENTRY_SELECTOR(
                        product, day, market, risk.iloc[ordinal], targets.iloc[ordinal], gov)
                    new_name = symbol("option", "core_put", new_contract)
                    new_price = market.quote(day, new_contract, "option")
                    sequence += 1
                    trade(account, day, signal_day, new_name, PositionTarget("option", new_qty * scale, lot.multiplier, spot_price), new_price, sequence)
                    core_entry = new_price
                    if REENTRY_SELECTOR is not None:
                        core_override = (new_contract, new_qty)
                    three_x_count += 1
            elif maintenance_today:
                core_entry = maintenance_core_entry
            else:
                core_entry = None
            if split_core and extra_units > 0:
                extra = next((n for n in account.positions if n.startswith("option|core_extra_put|")), None)
                if maintenance_today:
                    extra_entry = maintenance_extra_entry
                elif extra is None:
                    extra_entry = None
                elif extra_entry is None:
                    extra_entry = ordinary_extra_open_entry if ordinary_extra_open_entry is not None else account.positions[extra].mark
                if PROFIT_EXECUTION_PHASE == "open" and extra_profit3x_trigger == day:
                    if extra is None or extra_profit3x_open_entry is None:
                        raise RuntimeError("3x open execution did not buy an extra core Put")
                    extra_entry = extra_profit3x_open_entry
                    extra_three_x_count += 1
            extra_profit3x_trigger = None
            extra_profit3x_preselected = None
            profit3x_trigger = None
            profit3x_preselected = None
            if VERSION in {"v4", "v5", "v6", "v7"}:
                previous_target = target_signature
                for sleeve, signature in maintenance_signatures.items():
                    if signature is None:
                        previous_target.pop(sleeve, None)
                    else:
                        previous_target[sleeve] = signature
                if core_override is not None:
                    previous_target["core_put"] = core_override
                if extra_override is not None:
                    previous_target["core_extra_put"] = extra_override
        if QUARTER_ROLL_AT_SIGNAL_CLOSE and bool(targets.iloc[ordinal].quarter_roll_confirmed):
            new_contract = str(targets.iloc[ordinal].quarter_core_eod)
            for sleeve in ("core", "core_extra", "momentum", "grid"):
                old = next((name for name in account.positions if name.startswith(f"future|{sleeve}|")), None)
                if old is None or current_contract(old) == new_contract:
                    continue
                quantity = account.positions[old].quantity
                old_price = market.quote(day, current_contract(old), "future")
                new_price = market.quote(day, new_contract, "future")
                sequence += 1
                trade(account, day, day, old, PositionTarget("future", 0.0, 200, spot_price), old_price, sequence)
                account.entries[-1].update(quarter_roll=True, price_basis="T_close_confirmed_quarter_roll")
                sequence += 1
                trade(account, day, day, symbol("future", sleeve, new_contract),
                      PositionTarget("future", quantity, 200, spot_price), new_price, sequence)
                account.entries[-1].update(quarter_roll=True, price_basis="T_close_confirmed_quarter_roll")
                if sleeve in previous_target:
                    previous_target[sleeve] = (new_contract, previous_target[sleeve][1])
        future_notional = sum(abs(lot.quantity) * lot.multiplier * lot.mark for lot in account.positions.values() if lot.kind == "future")
        seller_notional = sum(abs(lot.quantity) * lot.multiplier * spot_price for name, lot in account.positions.items() if name.startswith("option|seller|"))
        if MARGIN_MODEL == "v1":
            seller_buffer = .3 * seller_notional
            call_buffer = 0.0
        else:
            seller_buffer = SELLER_NAV_BUFFER_RATE * account.equity() if seller_notional > 0 else 0.0
            call_buffer = 0.0
            for name, lot in account.positions.items():
                if not name.startswith("option|call|") or lot.quantity >= 0:
                    continue
                contract = current_contract(name)
                strike = float(contract.split("-")[-1])
                otm = max(strike - spot_price, 0.0)
                call_buffer += -lot.quantity * lot.multiplier * (
                    lot.mark + max(.12 * spot_price - otm, .07 * spot_price))
        account.reserve_margin(.3 * future_notional + seller_buffer + call_buffer)
        account.accrue_cash(day.isoformat(), max(0.0, account.cash - account.margin_reserved), CASH_DAILY)
        snap = account.snapshot(day.isoformat(), "close")
        history.append({"product": product, "arm": arm, "date": day.isoformat(), "equity_close": snap["equity"],
                        "cash_close": snap["cash"], "margin_reserved": snap["margin_reserved"],
                        "option_value": snap["option_value"], "etf_value": snap["etf_value"],
                        "seller_buffer": seller_buffer, "call_buffer": call_buffer,
                        "fees_cumulative": snap["fees_paid_cumulative"],
                        "seller_route": life.iloc[ordinal].route_before if ordinal < len(life) else None,
                        "profit3x_resets_to_date": three_x_count})
        if controller is not None:
            next_day = date.fromisoformat(str(risk.execution_date.iloc[ordinal])) if ordinal < len(risk) - 1 else None
            controller.close(day, next_day, account, risk.iloc[ordinal], targets.iloc[ordinal],
                             candidates.iloc[ordinal] if ordinal < len(candidates) else None)
            history[-1].update(controller.snapshot(account))
        if ordinal < len(risk) - 1:
            core = next((n for n in account.positions if n.startswith("option|core_put|")), None)
            if core is not None and core_entry is not None and not bool(targets.iloc[ordinal].monthly_reset_due):
                if account.positions[core].mark >= PROFIT_RESTRIKE_MULTIPLE * core_entry:
                    profit3x_trigger = date.fromisoformat(risk.execution_date.iloc[ordinal])
                    if PROFIT_EXECUTION_PHASE == "open":
                        if REENTRY_SELECTOR is None:
                            raise RuntimeError("causal 3x open requires a T-close contract selector")
                        profit3x_preselected = REENTRY_SELECTOR(
                            product, day, market, risk.iloc[ordinal], targets.iloc[ordinal], gov)
            if product == "IC" and FIXED_SELLER_CORE_UNITS is not None and CORE_UNITS > FIXED_SELLER_CORE_UNITS:
                extra = next((n for n in account.positions if n.startswith("option|core_extra_put|")), None)
                if extra is not None and extra_entry is not None and not bool(targets.iloc[ordinal].monthly_reset_due):
                    if account.positions[extra].mark >= PROFIT_RESTRIKE_MULTIPLE * extra_entry:
                        extra_profit3x_trigger = date.fromisoformat(risk.execution_date.iloc[ordinal])
                        if PROFIT_EXECUTION_PHASE == "open":
                            if REENTRY_SELECTOR is None:
                                raise RuntimeError("causal extra-core 3x open requires T-close selector")
                            extra_profit3x_preselected = REENTRY_SELECTOR(
                                product, day, market, risk.iloc[ordinal], targets.iloc[ordinal], gov)
    daily = pd.DataFrame(history)
    daily["nav"] = daily.equity_close / initial_capital
    daily["return_net"] = daily.nav.div(daily.nav.shift(1).fillna(1)).sub(1)
    results = metrics(daily.nav.to_numpy(float))
    events = pd.DataFrame(account.entries)
    audit = {"product": product, "arm": arm, "days": len(daily), "initial_capital": initial_capital,
             **results, "orders": int(events.event.eq("order_submitted").sum()),
             "fills": int(events.event.eq("trade").sum()), "profit3x_resets": three_x_count,
             "extra_core_profit3x_resets": extra_three_x_count,
             "maintenance_events": maintenance_events,
             "buyer_open_proxies": market.buyer_open_proxies,
             "lag_missing_contract_suppressed": lag_missing_contract_suppressed,
             "lag_fresh_put_selections": lag_fresh_put_selections,
             "fees_paid": float(account.fees_paid), "min_cash_after_reserve": float((daily.cash_close - daily.margin_reserved).min()),
             "model_execution": ("T close target; T+1 dated open futures/seller and synchronized core/Call exit; T+1 dated close ordinary option fills; no volume cap"
                                 if EXECUTION_MODEL == "mixed" else "T close target, T+1 dated close market-maker fill, no volume cap"),
             "costs": ("1bp futures turnover, 5bp option underlying turnover, 3% cash interest, 30% futures/seller buffer" if MARGIN_MODEL == "v1"
                       else "1bp futures turnover, 5bp Put and 1bp Call underlying turnover, 3% cash interest, 30% futures buffer, 15% NAV seller buffer, modeled short-Call buffer" if VERSION in {"v4", "v5", "v6", "v7"}
                       else "1bp futures turnover, 5bp option underlying turnover, 3% cash interest, 30% futures buffer, 15% NAV seller buffer, modeled short-Call buffer"),
             "classification": "native_fix4_model_account_pending_independent_audit"}
    if controller is not None:
        audit.update(controller.audit())
    else:
        audit['recovery_research_mode'] = 'A0'
    return daily, events, audit


def main() -> None:
    out = HERE / f"native_fix4_account_{VERSION}_daily.csv.gz"
    journal = HERE / f"native_fix4_account_{VERSION}_events.csv.gz"
    summary = HERE / f"native_fix4_account_{VERSION}.json"
    if out.exists() or journal.exists() or summary.exists():
        raise FileExistsError("first account output frozen")
    source = DatedSources()
    lifecycle = pd.read_csv(HERE / f"native_fix4_seller_lifecycle_{LIFECYCLE_VERSION}.csv.gz")
    candidates = pd.read_csv(HERE / "native_fix4_seller_candidates_v1.csv.gz")
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv", parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    dailies, journals, audits = [], [], []
    for product in ("IC", "IM"):
        for arm in ("no_seller", "one_roll", "repeat_roll"):
            daily, events, audit = replay(product, arm, source, lifecycle, candidates, gov, spot)
            dailies.append(daily)
            events.insert(0, "product", product)
            events.insert(1, "arm", arm)
            journals.append(events)
            audits.append(audit)
            print(json.dumps(audit, ensure_ascii=False), flush=True)
    pd.concat(dailies, ignore_index=True).to_csv(out, index=False, compression="gzip")
    pd.concat(journals, ignore_index=True).to_csv(journal, index=False, compression="gzip")
    summary.write_text(json.dumps({"formal_producer_sha256": sha256(ROOT / "poe_ic_im_mainline_v1_4_bot.py"),
                                   "paths": audits, "no_inherited_strategy_targets": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
