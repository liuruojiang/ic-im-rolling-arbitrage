"""Independently recompute saved returns, fees, input hashes and quote checks."""
from __future__ import annotations
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
sys.path[:0]=[str(ROOT),str(NATIVE)]
import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
from native_fix4_historical_sources_v2 import DatedSources
from native_fix4_historical_sources_v1 import IC_DATA, IC_FUTURES, IM_FUTURES, IM_ARCHIVES


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def main():
    d=pd.read_csv(HERE/'daily_paths.csv.gz');e=pd.read_csv(HERE/'events.csv.gz',low_memory=False)
    m=pd.read_csv(HERE/'window_metrics.csv')
    checks=[]
    for (product,arm),z in d.groupby(['product','arm'],sort=False):
        z=z.reset_index(drop=True); nav=np.cumprod(1+z.return_net.to_numpy(float))
        nav_error=float(np.max(np.abs(nav-z.nav.to_numpy(float))))
        trades=e.loc[(e['product']==product)&e.arm.eq(arm)&e.event.eq('trade')]
        fee_error=float(abs(trades.fee.fillna(0).sum()-z.fees_cumulative.iloc[-1]))
        assert nav_error<1e-11 and fee_error<1e-6
        for metric in m.loc[(m['product']==product)&m.arm.eq(arm)&m.status.eq('OK')].itertuples():
            w=z.loc[z.date.ge(metric.start)]
            n=np.r_[1.,np.cumprod(1+w.return_net.to_numpy(float))]
            assert abs(n[-1]**(252/len(w))-1-metric.cagr)<1e-11
            assert abs((n/np.maximum.accumulate(n)-1).min()-metric.max_dd)<1e-11
        checks.append(dict(product=product,arm=arm,nav_error=nav_error,fee_error=fee_error,rows=len(z)))
    # Exercise the actual production opening-price adapter on every repaired
    # seller entry/roll date. States are injected, prices are frozen raw data.
    source=DatedSources(); quote_checks=[]
    for product in ('IC','IM'):
        life=pd.read_csv(HERE/f'{product.lower()}_seller_lifecycle_fixed.csv.gz')
        targets=pd.read_csv(HERE/'inputs'/product.lower()/f'native_fix4_{product.lower()}_noseller_targets_{"v2" if product=="IC" else "v3"}.csv.gz')
        previous_contract=None;previous_price=None;previous_expiry=None;previous_security=None
        for row in life.itertuples():
            if row.action not in {'ENTER_SHORT_PUT','ROLL_SHORT_PUT'}:continue
            day=date.fromisoformat(row.execution_date);signal_day=date.fromisoformat(row.signal_date)
            target_row=targets.loc[targets.date.eq(row.signal_date)].iloc[0] if 'date' in targets else targets.loc[targets.signal_date.eq(row.signal_date)].iloc[0]
            contract=row.active_contract_after
            security=None
            if product=='IC':
                chain=source.ic_on(day)[1]
                found=chain.loc[chain.contract.eq(contract)].iloc[0]
                security=str(int(found.security_id))
                expiry=bot._fourth_wednesday(int('20'+contract[7:9]),int(contract[9:11]))
            else:
                expiry=bot._third_friday(2000+int(contract[2:4]),int(contract[4:6]))
            a=policy.default_extension(product)
            a['post_core_contract']=str(target_row.quarter_core_eod)
            if row.action=='ROLL_SHORT_PUT':
                a.update(v14_route_state='short_put',v14_cycle_id='raw_quote_adapter_validation',
                    v14_short_put_contract=previous_contract,v14_short_put_security_id=previous_security,
                    v14_short_put_qty_normalized=1.5 if product=='IM' else 1.,
                    v14_short_put_entry_premium=previous_price,v14_short_put_expiry=previous_expiry)
            t={**a,'v14_route_state':'short_put','v14_short_put_contract':contract,
               'v14_short_put_security_id':security,'v14_short_put_qty_normalized':1.5 if product=='IM' else 1.,
               'v14_short_put_entry_premium':float(row.candidate_premium_t if row.action=='ENTER_SHORT_PUT' else row.roll_candidate_premium_t),
               'v14_short_put_expiry':expiry}
            plan=execution.short_plan(product,dict(market_date=signal_day,next_trade_date=day),a,t,row.action)
            def quotes(key,clock):
                if key=='MO':frame=source.im_mo_on(day).copy()
                else:frame=source.futures_on(key,day).rename(columns={'contract':'instrument','open':'openprice'}).copy()
                frame.attrs.update(source_date=day,source='frozen_CFFEX_official_daily_archive')
                return frame
            def ic_open(sec,when):
                assert when==day
                chain=source.ic_on(day)[1]
                rows=chain.loc[chain.security_id.astype(int).astype(str).eq(str(sec))]
                assert len(rows)==1
                return float(rows.open.iloc[0]),'frozen_Sina_dated_option_OHLC'
            clock=datetime(day.year,day.month,day.day,16,tzinfo=bot.BEIJING)
            with patch.object(bot,'fetch_cffex_quotes',side_effect=quotes),patch.object(bot,'fetch_option_dated_open',side_effect=ic_open):
                prices=bot._v14_committed_open_prices(product,plan,day,clock,{})
            actual=execution.checked_prices(plan,prices,day)[(contract,security,'option')]
            assert actual==float(row.fill_new_t1)==float(row.entry_premium_state_after)
            quote_checks.append(dict(product=product,signal_day=str(signal_day),execution_day=str(day),
                contract=contract,actual_open=actual,required_legs=len(prices),status='PASS'))
            previous_contract,previous_price,previous_expiry,previous_security=contract,actual,expiry,security
    guards=json.loads((HERE/'backup_manifest.json').read_text(encoding='utf-8'))['guarded']
    changed=[name for name,h in guards.items() if sha(ROOT/name)!=h]
    assert not changed,changed
    source_files=[IC_DATA/'sse_510500_standard_put_snapshots.csv.gz',IC_DATA/'sina_510500_put_histories.csv.gz',
                  IC_DATA/'sina_510500_etf.csv',IC_FUTURES,IM_FUTURES]
    months=sorted({str(x).replace('-','')[:6] for x in d.loc[d['product'].eq('IM'),'date']})
    source_files.extend(IM_ARCHIVES/(month+'.zip') for month in months)
    xml=ET.parse(HERE/'regression.xml').getroot()
    tests=sum(int(x.get('tests','0')) for x in xml.iter('testsuite'))
    failures=sum(int(x.get('failures','0'))+int(x.get('errors','0')) for x in xml.iter('testsuite'))
    result=dict(saved_output_checks=checks,production_adapter_real_quote_checks=quote_checks,
                immutable_guarded_files=len(guards),changed_guarded_files=changed,
                regression=dict(tests=tests,failures_or_errors=failures),
                input_sha256={str(p.relative_to(ROOT)):sha(p) for p in source_files},
                corrected_lifecycle_label='The inherited lifecycle summary entry_basis label is stale; numeric entry_premium_state_after equals fill_new_t1 for all 11 entries/rolls.',
                limitations=['Ordinary IM buyer open uses 4 inherited sticky-IV proxy fills per arm; seller cost repair does not introduce proxy fills.',
                             'Historical IC contract membership is reconstructed; daily OHLC does not establish executable bid/ask or capacity.',
                             'The historical account engine already handles 3x at open; timing-only is a verified parity control, not an artificially altered strategy.'])
    (HERE/'independent_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output_checks=len(checks),real_quote_checks=len(quote_checks),immutable_files=len(guards),tests=tests,failures=failures)))


if __name__=='__main__':main()
