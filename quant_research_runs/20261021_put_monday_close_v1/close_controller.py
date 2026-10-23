"""Close maintenance subclass; ordinary execution remains in the prior engine."""
from datetime import date
import pandas as pd


def make_close_class(base):
    class CloseController(base.Controller):
        close_mode=True

        def __init__(self,*args):
            super().__init__(*args)
            self.today_close_reset=False
            self.close_cost=None
            self.frozen=None

        def target_for_open(self,day,signal_day,r,t):
            self.today_close_reset=False
            self.today_open_reset=False
            self.close_cost=None
            if self.pending is not None:
                if (self.pending['due'] is not None and day>=self.pending['due']
                        and self.frozen is None):
                    self.frozen=(signal_day,r.copy(),t.copy())
                return t.copy()
            return super().target_for_open(day,signal_day,r,t)

        def enforce_pending(self,day,desired,account,route):
            arm=self.arm
            self.arm='carry_old'
            try:
                super().enforce_pending(day,desired,account,route)
            finally:
                self.arm=arm

        def schedule_close(self,day,account,spot,sequence):
            self.frozen=None
            return super().schedule_close(day,account,spot,sequence)

        def close_selection(self,day,decision_day,r,t,sleeve):
            z=self.select(day,decision_day,r,t,'close',sleeve)
            name='contract' if self.product=='IC' else 'instrument'
            chain=self.market.option_chain(day)
            expiries={self.e.option_expiry(self.product,c) for c in chain[name].astype(str) if 'P' in c}
            previous=next(d for d in reversed(self.calendar) if d<day)
            before={self.e.option_expiry(self.product,c)
                    for c in self.market.option_chain(previous)[name].astype(str) if 'P' in c}
            anchor=(pd.Timestamp(day)+pd.DateOffset(months=3)).date()
            return z,dict(product=self.product,arm=self.arm,date=str(day),signal_date=str(decision_day),
                prior_session=str(previous),sleeve=sleeve,contract=z['contract'],expiry=str(z['expiry']),
                target_expiry=str(anchor),remaining_days=(z['expiry']-day).days,
                target_error_days=abs((z['expiry']-anchor).days),price=z['price'],
                newly_listed_month=z['expiry'] not in before,
                listed_expiries=';'.join(map(str,sorted(expiries))))

        def execute_close(self,day,r,t,account,route,sequence):
            if self.pending is None or self.pending['due'] is None or day<self.pending['due']:
                return sequence,{}
            if self.arm=='close_current':
                decision_day=day
            else:
                decision_day,r,frozen_t=self.frozen
                # Same-day held-future identity is observable at close even in
                # the frozen-risk control; no obsolete future sizes the Put.
                original=t
                t=frozen_t.copy()
                t['quarter_core_eod']=original.quarter_core_eod
                t['future_close']=original.future_close
            trial={}
            rows=[]
            try:
                if self.product=='IC':
                    deltas=(float(r.core_put_target_delta),float(r.momentum_put_target_delta))
                    if sum(deltas)>0:
                        trial['shared'],row=self.close_selection(day,decision_day,r,t,'shared')
                        rows.append(row)
                else:
                    deltas=None
                    for sleeve,qty in [('core',r.core_put_target_qty_normalized),
                                       ('momentum',r.momentum_put_target_qty_normalized)]:
                        if qty>0:
                            trial[sleeve],row=self.close_selection(day,decision_day,r,t,sleeve)
                            rows.append(row)
                # Verify both old and new legs before committing either side.
                for n in account.positions:
                    if n.startswith(('option|core_put|','option|momentum_put|')):
                        self.market.quote(day,self.e.current_contract(n),'option')
            except RuntimeError as exc:
                self.failures.append(dict(product=self.product,arm=self.arm,date=str(day),
                    stage='monthly_close_missing_evidence',error=str(exc)))
                return sequence,{}
            scale=account.equity()/(float(t.future_close)*200)
            assert scale>0
            signatures={}
            for sleeve in ('core_put','momentum_put'):
                key='shared' if self.product=='IC' else ('core' if sleeve=='core_put' else 'momentum')
                z=trial.get(key,{})
                qtykey='core_qty' if sleeve=='core_put' else 'momentum_qty'
                qty=z.get(qtykey,0.)
                qty*=self.e.CORE_BUYER_SCALE if sleeve=='core_put' else self.e.MOMENTUM_SCALE
                if (sleeve=='core_put' and route!='future') or 'buyer_put' in self.e.DISABLED_MODULES:
                    qty=0.
                if sleeve=='momentum_put' and 'momentum' in self.e.DISABLED_MODULES:
                    qty=0.
                contract=z.get('contract')
                desired_name=self.e.symbol('option',sleeve,contract) if qty>0 else None
                for n,lot in list(account.positions.items()):
                    if n.startswith(f'option|{sleeve}|') and n!=desired_name:
                        sequence+=1
                        self.e.trade(account,day,decision_day,n,
                            self.e.PositionTarget('option',0.,lot.multiplier,self.market.spot(day)),
                            self.market.quote(day,self.e.current_contract(n),'option'),sequence,
                            phase='close',price_basis='Monday_monthly_maintenance_actual_close')
                if desired_name is not None:
                    sequence+=1
                    self.e.trade(account,day,decision_day,desired_name,
                        self.e.PositionTarget('option',qty*scale,10000 if self.product=='IC' else 100,
                                              self.market.spot(day)),
                        z['price'],sequence,phase='close',
                        price_basis='Monday_monthly_maintenance_actual_close')
                    signatures[sleeve]=(contract,qty)
                    if sleeve=='core_put':self.close_cost=z['price']
                else:
                    signatures[sleeve]=None
            self.state=trial
            self.last_risk_delta=deltas
            self.selections.extend(rows)
            self.pending['executed']=str(day)
            self.pending['status']='closed_maintenance' if trial else 'zero_target'
            self.pending=None
            self.frozen=None
            self.today_close_reset=True
            return sequence,signatures
    return CloseController
