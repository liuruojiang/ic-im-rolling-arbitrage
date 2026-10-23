"""Monthly Put wait for actually observed new ETF expiry-month membership."""
import pandas as pd


def make_classes(close_base):
    class SettlementFrozen(close_base):
        def __init__(self,*args):
            super().__init__(*args)
            self.risk_table=args[3].set_index('signal_date',drop=False)
            self.target_table=args[4].set_index('signal_date',drop=False)
        def schedule_close(self,day,account,spot,sequence):
            seq=super().schedule_close(day,account,spot,sequence)
            self.frozen=(day,self.risk_table.loc[str(day)].copy(),self.target_table.loc[str(day)].copy())
            return seq

    class WaitListing(SettlementFrozen):
        def __init__(self,*args):
            super().__init__(*args)
            self.wait_rows=[]
            self.anchor_months=set()
            self.in_maintenance=False
        def months(self,day):
            # Only today's known contract metadata. No future-row calendar.
            c=self.market.option_chain(day)
            return set(c.contract_month.dt.strftime('%Y%m'))
        def schedule_close(self,day,account,spot,sequence):
            seq=super().schedule_close(day,account,spot,sequence)
            self.anchor_months=self.months(day)
            self.pending['due']=None
            self.pending['anchor_months']=';'.join(sorted(self.anchor_months))
            self.pending['listing_observed']=None
            self.pending['new_months']=None
            return seq
        def target_for_open(self,day,signal_day,r,t):
            if self.pending is not None and self.pending['due'] is None:
                current=self.months(day)
                extra=current-self.anchor_months
                self.wait_rows.append(dict(arm=self.arm,signal_day=str(self.frozen[0]),date=str(day),
                    months=';'.join(sorted(current)),new_months=';'.join(sorted(extra))))
                if extra:
                    self.pending['due']=day
                    self.pending['listing_observed']=str(day)
                    self.pending['new_months']=';'.join(sorted(extra))
            return super().target_for_open(day,signal_day,r,t)
        def select(self,day,signal_day,r,t,phase,sleeve,fixed_contract=None):
            if not (self.arm=='wait_min3' and self.in_maintenance and fixed_contract is None):
                return super().select(day,signal_day,r,t,phase,sleeve,fixed_contract)
            original_market=self.market
            floor=(pd.Timestamp(day)+pd.DateOffset(months=3)).date()
            e=self.e
            class Eligible:
                def option_chain(self,d):
                    c=original_market.option_chain(d)
                    if d==day:
                        c=c.loc[c.contract.map(lambda x:e.option_expiry('IC',str(x))>=floor)].copy()
                    return c
                def __getattr__(self,n):return getattr(original_market,n)
            self.market=Eligible()
            try:
                return super().select(day,signal_day,r,t,phase,sleeve,fixed_contract)
            finally:
                self.market=original_market
        def execute_close(self,*args):
            self.in_maintenance=True
            try:return super().execute_close(*args)
            finally:self.in_maintenance=False
    return SettlementFrozen,WaitListing
