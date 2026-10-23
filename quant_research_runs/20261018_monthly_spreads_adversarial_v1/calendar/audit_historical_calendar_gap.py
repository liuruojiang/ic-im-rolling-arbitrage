"""Reproduce uncovered historical holiday expiries from complete saved contracts.

The final observed contract date is an ex-post audit oracle only. It must not
be substituted into point-in-time trading/research rules or historical ledgers.
"""
import json
import hashlib
import sys
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import pandas as pd
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot

SOURCES={'IC':'data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv',
         'IM':'data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv'}
CASES=[('IC','2016-09-19','IC1609'),('IC','2018-02-22','IC1802'),
       ('IC','2024-02-19','IC2402'),('IM','2024-02-19','IM2402')]
frames={p:pd.read_csv(ROOT/path) for p,path in SOURCES.items()}
result={'classification':'historical dates outside official coverage; not a 2026 monthly feature regression',
        'official_calendar_years':sorted(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS),
        'cases':[],'fixtures':[]}
for p,path in SOURCES.items():
    result['fixtures'].append({'path':path,'sha256':hashlib.sha256((ROOT/path).read_bytes()).hexdigest()})
for p,ds,c in CASES:
    raw=frames[p]
    last=raw.groupby('contract').date.max().map(date.fromisoformat).to_dict()
    data=raw.loc[raw.date.eq(ds)].copy()
    f=data.rename(columns={'contract':'instrument','close':'lastprice'})
    f.attrs.update(source='saved official CFFEX close',source_date=ds)
    fallback=lambda leg:bot._third_friday(*bot._contract_month(leg))
    # All legs used by these four cases end years before file truncation.
    assert all(last[leg] < date(2026,1,1) for leg in data.contract)
    actual={'quarter_spread':q.quarter_spread(p,f,date.fromisoformat(ds),fallback),
            **q.monthly_spreads(p,f,date.fromisoformat(ds),fallback)}
    verified={'quarter_spread':q.quarter_spread(p,f,date.fromisoformat(ds),last.__getitem__),
              **q.monthly_spreads(p,f,date.fromisoformat(ds),last.__getitem__)}
    record={'product':p,'date':ds,'front_contract':c,
            'fallback_expiry':str(fallback(c)),'last_actual_official_trading_date':str(last[c]),
            'all_input_contract_last_dates':{leg:str(last[leg]) for leg in data.contract},
            'input_quotes':data[['contract','date','close']].to_dict('records'),
            'actual_weekday_fallback':actual,'ex_post_actual_expiry_oracle':verified,
            'coverage_note':bot._calendar_coverage_note([fallback(c)])}
    assert verified['month_next_spread']['status']=='ok'
    assert actual['month_next_spread']['status']=='unavailable'
    assert last[c] == date.fromisoformat(ds)
    result['cases'].append(record)
out=Path(__file__).with_name('historical_calendar_gap_results.json')
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'product':v['product'],'date':v['date'],
                  'fallback_expiry':v['fallback_expiry'],'actual_expiry':v['last_actual_official_trading_date'],
                  'month_next_actual':v['actual_weekday_fallback']['month_next_spread'],
                  'month_next_expected':v['ex_post_actual_expiry_oracle']['month_next_spread'],
                  'month_quarter_actual':v['actual_weekday_fallback']['month_quarter_spread'],
                  'month_quarter_expected':v['ex_post_actual_expiry_oracle']['month_quarter_spread']}
                 for v in result['cases']],ensure_ascii=True,indent=2))
