"""Research-only long-Put maintenance controller; never reads a live ledger."""
from datetime import date, timedelta
import math
import pandas as pd


def next_monday_session(day, calendar):
    monday = day + timedelta(days=(7 - day.weekday()) % 7 or 7)
    return next((d for d in calendar if d >= monday), None)


class Controller:
    def __init__(self, engine, arm, product, risk, targets, market, gov):
        self.e, self.arm, self.product = engine, arm, product
        self.calendar = [date.fromisoformat(s) for s in risk.signal_date]
        self.market, self.gov = market, gov
        self.pending = None
        self.enabled = False
        self.state = {}
        self.plans, self.selections, self.failures = [], [], []
        self.today_open_reset = False
        self.gap_sessions = 0
        self.last_risk_delta = None

    def select(self, day, signal_day, r, t, phase, sleeve, fixed_contract=None):
        # Only instrument identities, expiry, strike and the requested price phase
        # enter selection. Dated close/volume/OI are deliberately never read.
        e, market = self.e, self.market
        chain = market.option_chain(day)
        name = 'contract' if self.product == 'IC' else 'instrument'
        price_col = ('open' if phase == 'open' else 'last') if self.product == 'IC' else (
            'openprice' if phase == 'open' else 'lastprice')
        q = chain.loc[chain[name].astype(str).str.contains('P')].copy()
        q['contract_key'] = q[name].astype(str)
        q['expiry_key'] = q.contract_key.map(lambda c: e.option_expiry(self.product, c))
        q['strike_key'] = q.contract_key.map(lambda c: float(c.split('M')[-1])/1000
                                           if self.product == 'IC' else float(c.split('-')[-1]))
        q = q.loc[q.expiry_key.gt(day)]
        if q.empty:
            raise RuntimeError(f'No listed future Put {self.product} {day}')
        anchor = (pd.Timestamp(day)+pd.DateOffset(months=3)).date()
        if fixed_contract:
            chosen = q.loc[q.contract_key.eq(fixed_contract)]
        else:
            expiries = sorted(set(q.expiry_key))
            expiry = min(expiries, key=lambda d: (abs((d-anchor).days),
                         d.toordinal() if self.product == 'IC' else -d.toordinal()))
            # Strike frozen using the last completed session, not today's close.
            reference = market.spot(signal_day)
            target = reference*(.95 if self.product == 'IC' else 1.02)
            chosen = q.loc[q.expiry_key.eq(expiry)].copy()
            chosen['strike_error'] = (chosen.strike_key-target).abs()
            chosen = chosen.sort_values(['strike_error','contract_key']).iloc[:1]
        if len(chosen) != 1:
            raise RuntimeError(f'Put absent from dated listing: {day} {fixed_contract}')
        z = chosen.iloc[0]
        price = float(z[price_col])
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError(f'No positive exact {phase}: {day} {z.contract_key}')
        out = dict(contract=z.contract_key, expiry=z.expiry_key, price=price)
        if self.product == 'IC':
            underlying = market.spot_open(day) if phase == 'open' else market.spot(day)
            # The sizing future quote must match the same observable phase.
            fut = market.quote(day, str(t.quarter_core_eod), 'future', phase=phase)
            rate = float(self.gov.asof(pd.Timestamp(signal_day)))
            div = float(e.formal.FROZEN['IC']['dividend'])
            tau = max((z.expiry_key-day).days, 1)/365
            iv = e.formal._implied_volatility('P', price, underlying, float(z.strike_key), rate, div, tau)
            if iv is None:
                raise RuntimeError(f'IC IV unavailable {day} {z.contract_key} {price}')
            delta = abs(e.formal._bs_price_delta('P', underlying,float(z.strike_key),rate,div,iv,tau)[1])
            if not delta > 1e-8:
                raise RuntimeError('IC invalid Delta')
            full = max(1, round(fut*200/(underlying*10000)))
            total_delta = float(r.core_put_target_delta+r.momentum_put_target_delta)
            total_qty = max(1, round(full*total_delta/delta)) if total_delta > 0 else 0
            parts = e.formal._ic_put_quantity_breakdown(full,delta,float(r.core_put_target_delta),
                                                      float(r.momentum_put_target_delta),total_qty)
            out.update(core_qty=float(parts['core']), momentum_qty=float(parts['momentum']), delta=delta)
        else:
            out.update(core_qty=float(r.core_put_target_qty_normalized),
                       momentum_qty=float(r.momentum_put_target_qty_normalized))
        if phase == 'open' and not fixed_contract:
            prior_names = set(market.option_chain(signal_day)[name].astype(str))
            prior_expiries = {e.option_expiry(self.product, c) for c in prior_names if 'P' in c}
            self.selections.append(dict(product=self.product,arm=self.arm,date=str(day),
                signal_date=str(signal_day),sleeve=sleeve,contract=z.contract_key,
                expiry=str(z.expiry_key),target_expiry=str(anchor),remaining_days=(z.expiry_key-day).days,
                target_error_days=abs((z.expiry_key-anchor).days),price=price,
                newly_listed_month=z.expiry_key not in prior_expiries,
                previous_listed_expiries=';'.join(map(str,sorted(prior_expiries))),
                listed_expiries=';'.join(map(str,sorted(set(q.expiry_key))))))
        return out

    def target_for_open(self, day, signal_day, r, t):
        self.today_open_reset = False
        t = t.copy()
        if not self.enabled:
            return t
        is_due = self.pending is not None and self.pending['due'] is not None and day >= self.pending['due']
        if self.pending is not None and not is_due:
            self.gap_sessions += int(self.arm == 'buy_only')
            return t
        deltas = (float(r.core_put_target_delta),float(r.momentum_put_target_delta)) if self.product=='IC' else None
        if is_due:
            # Both sleeves use the last fully completed signal. The actual open
            # of a newly listed contract is required; never proxy with its close.
            trial = {}
            try:
                if self.product == 'IC':
                    if sum(deltas)>0:
                        trial['shared'] = self.select(day,signal_day,r,t,'open','shared')
                else:
                    for sleeve,qty in [('core',r.core_put_target_qty_normalized),
                                       ('momentum',r.momentum_put_target_qty_normalized)]:
                        if qty>0:
                            trial[sleeve] = self.select(day,signal_day,r,t,'open',sleeve)
            except RuntimeError as exc:
                self.failures.append(dict(date=str(day),arm=self.arm,product=self.product,error=str(exc)))
                return t
            self.state = trial
            self.last_risk_delta = deltas
            self.pending['executed'] = str(day)
            self.pending['status'] = 'opened' if trial else 'zero_target'
            self.pending = None
            self.today_open_reset = True
        if self.product == 'IC':
            if sum(deltas) <= 0:
                self.state = {}
            elif 'shared' not in self.state:
                self.state['shared'] = self.select(signal_day,signal_day,r,t,'close','shared')
            elif self.last_risk_delta != deltas:
                self.state['shared'] = self.select(signal_day,signal_day,r,t,'close','shared',
                                                  self.state['shared']['contract'])
            self.last_risk_delta = deltas
            z = self.state.get('shared',{})
            t['core_put_contract_target'] = z.get('contract')
            t['core_put_qty_target'] = z.get('core_qty',0)
            t['momentum_put_qty_target'] = z.get('momentum_qty',0)
        else:
            for sleeve,qty in [('core',float(r.core_put_target_qty_normalized)),
                               ('momentum',float(r.momentum_put_target_qty_normalized))]:
                if qty <= 0:
                    self.state.pop(sleeve,None)
                elif sleeve not in self.state:
                    self.state[sleeve] = self.select(signal_day,signal_day,r,t,'close',sleeve)
                t[f'{sleeve}_put_contract_target'] = self.state.get(sleeve,{}).get('contract')
                t[f'{sleeve}_put_qty_target'] = qty
        return t

    def enforce_pending(self, day, desired, account, route):
        if self.pending is None:
            return
        for sleeve in ('core_put','momentum_put'):
            prefix = f'option|{sleeve}|'
            positive = any(n.startswith(prefix) for n in desired)
            for n in list(desired):
                if n.startswith(prefix):
                    desired.pop(n)
            if self.arm == 'carry_old' and positive and (sleeve != 'core_put' or route=='future'):
                for n,lot in account.positions.items():
                    if n.startswith(prefix) and self.e.option_expiry(self.product,self.e.current_contract(n))>day:
                        desired[n] = self.e.PositionTarget('option',lot.quantity,lot.multiplier,
                                                         self.market.spot_open(day))

    def profit_open_available(self, day, account, preselected):
        required = [preselected[0]]
        required += [self.e.current_contract(n) for n in account.positions
                     if n.startswith('option|core_put|')]
        try:
            for c in set(required):
                self.market.quote(day,c,'option',phase='open')
        except RuntimeError as exc:
            self.failures.append(dict(date=str(day),arm=self.arm,product=self.product,
                stage='profit3x_blocked_retain_existing',error=str(exc)))
            return False
        return True

    def schedule_close(self, day, account, spot, sequence):
        due = next_monday_session(day,self.calendar)
        self.pending = dict(product=self.product,arm=self.arm,settlement=str(day),
                            due=due,status='awaiting',executed=None)
        self.plans.append(self.pending)
        self.enabled = True
        self.state = {}
        if self.arm == 'buy_only':
            for name,lot in list(account.positions.items()):
                if name.startswith(('option|core_put|','option|momentum_put|')):
                    sequence+=1
                    self.e.trade(account,day,day,name,self.e.PositionTarget('option',0.,lot.multiplier,spot),
                        self.market.quote(day,self.e.current_contract(name),'option'),sequence,
                        price_basis='T_settlement_close_old_put_sale_delayed_buy')
        return sequence
