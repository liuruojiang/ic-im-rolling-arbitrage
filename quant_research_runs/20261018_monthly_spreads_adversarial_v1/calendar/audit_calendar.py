"""Independent contract-calendar oracle and saved-data audit; no network/state writes."""
from __future__ import annotations
import calendar
import hashlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pandas as pd
import ic_im_quarter_roll_v1_3 as target
import poe_ic_im_mainline_v1_4_bot as bot

OUT = Path(__file__).resolve().parent
# Explicit exchange closure facts, independent of production constant/function.
CLOSED_2026 = {date.fromisoformat(s) for s in (
    '2026-01-01 2026-01-02 2026-02-16 2026-02-17 2026-02-18 '
    '2026-02-19 2026-02-20 2026-02-23 2026-04-06 2026-05-01 '
    '2026-05-04 2026-05-05 2026-06-19 2026-09-25 '
    '2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07').split()}

def month_after(y, m, n=1):
    value = y * 12 + m - 1 + n
    return value // 12, value % 12 + 1

def oracle_expiry(y, m):
    first = date(y, m, 1)
    result = first + timedelta(days=(4-first.weekday()) % 7 + 14)
    while result.weekday() > 4 or result in CLOSED_2026:
        result += timedelta(days=1)
    return result

def ex(c):
    return bot._third_friday(2000 + int(c[2:4]), int(c[4:6]))

def oracle_pair_names(p, day):
    y, m = day.year, day.month
    if day > oracle_expiry(y, m):
        y, m = month_after(y, m)
    front = f'{p}{y % 100:02d}{m:02d}'
    yn, mn = month_after(y, m)
    next_c = f'{p}{yn % 100:02d}{mn:02d}'
    yq, mq = y, m
    while mq % 3:
        yq, mq = month_after(yq, mq)
    quarter_c = f'{p}{yq % 100:02d}{mq:02d}'
    return front, next_c, quarter_c

def expected_listing(p, day):
    front, nxt, _ = oracle_pair_names(p, day)
    y, m = 2000 + int(nxt[2:4]), int(nxt[4:])
    legs = [front, nxt]
    while len(legs) < 4:
        y, m = month_after(y, m)
        if m % 3 == 0:
            legs.append(f'{p}{y % 100:02d}{m:02d}')
    return legs

failures = []
counts = {'synthetic_product_days': 0, 'real_product_days': 0, 'real_pairs': 0,
          'front_missing_guard_cases': 0, 'expiry_calendar_months': 0}
boundaries = []
limitations = []

for m in range(1, 13):
    wanted = oracle_expiry(2026, m)
    actual = bot._third_friday(2026, m)
    counts['expiry_calendar_months'] += 1
    if wanted != actual:
        failures.append({'kind': 'expiry', 'month': m, 'expected': str(wanted), 'actual': str(actual)})

day = date(2026, 1, 1)
while day.year == 2026:
    if day.weekday() < 5 and day not in CLOSED_2026:
        for p in ('IC', 'IM'):
            contracts = expected_listing(p, day)
            f = pd.DataFrame({'instrument': contracts, 'lastprice': [6100., 6075., 6000., 5900.]})
            f.attrs.update(source_date=str(day), source='synthetic oracle', listed_instruments=contracts)
            actual = target.monthly_spreads(p, f, day, ex)
            near, next_c, quarter_c = oracle_pair_names(p, day)
            for key, far in [('month_next_spread', next_c), ('month_quarter_spread', quarter_c)]:
                v = actual[key]
                if v.get('status') != 'ok' or (v.get('near_contract'), v.get('far_contract')) != (near, far):
                    failures.append({'kind':'pair_selection', 'day':str(day), 'product':p, 'key':key, 'actual':v, 'expected':[near,far]})
                else:
                    near_exp = oracle_expiry(2000 + int(near[2:4]), int(near[4:]))
                    far_exp = oracle_expiry(2000 + int(far[2:4]), int(far[4:]))
                    price_map = dict(zip(contracts, [6100., 6075., 6000., 5900.]))
                    wanted_points = price_map[near] - price_map[far]
                    wanted_ratio = wanted_points / price_map[near]
                    wanted_gap = (far_exp - near_exp).days
                    wanted_annualized = wanted_ratio * 365 / wanted_gap if wanted_gap else None
                    numeric_ok = (v['expiry_gap_days'] == wanted_gap
                        and math.isclose(v['points'], wanted_points, abs_tol=1e-10)
                        and math.isclose(v['ratio'], wanted_ratio, abs_tol=1e-12)
                        and (v['annualized'] is None if wanted_annualized is None else math.isclose(v['annualized'], wanted_annualized, abs_tol=1e-12)))
                    if not numeric_ok:
                        failures.append({'kind':'calendar_numeric','day':str(day),'product':p,'key':key,'actual':v,
                                         'expected':[wanted_points,wanted_ratio,wanted_gap,wanted_annualized]})
            qs = target.quarter_spread(p, f, day, ex)
            if qs['status'] != 'ok':
                failures.append({'kind':'quarter_selection','day':str(day),'product':p,'actual':qs})
            counts['synthetic_product_days'] += 1
            missing = f.iloc[1:].copy()
            missing.attrs = f.attrs.copy()
            v = target.monthly_spreads(p, missing, day, ex)
            if any(x['status'] != 'unavailable' for x in v.values()):
                failures.append({'kind':'missing_front_quote','day':str(day),'product':p,'actual':v})
            try:
                bot._validate_complete_future_structure(missing, p, day, 'test', set(contracts[1:]))
            except RuntimeError:
                pass
            else:
                failures.append({'kind':'missing_front_listing_guard', 'day':str(day),'product':p})
            counts['front_missing_guard_cases'] += 1
            if day in {date(2026,2,24), date(2026,2,25), date(2026,6,18), date(2026,6,22), date(2026,6,23), date(2026,12,18), date(2026,12,21)}:
                boundaries.append({'product':p, 'date':str(day), 'pairs':actual})
    day += timedelta(days=1)

fixtures = []
for p, rel in [('IC','data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv'),
               ('IM','data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv')]:
    path = ROOT / rel
    raw = pd.read_csv(path)
    raw = raw.loc[raw.date.str.startswith('2026-')]
    fixtures.append({'path':rel, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                     'dates':[raw.date.min(),raw.date.max()], 'rows':len(raw)})
    for ds, data in raw.groupby('date'):
        day = date.fromisoformat(ds)
        f = data.rename(columns={'contract':'instrument','close':'lastprice'}).copy()
        f.attrs.update(source_date=ds, source='saved official close')
        values = target.monthly_spreads(p, f, day, ex)
        near, next_c, quarter_c = oracle_pair_names(p, day)
        prices = dict(zip(data.contract, data.close))
        for key, far in [('month_next_spread',next_c),('month_quarter_spread',quarter_c)]:
            v = values[key]
            expected_points = float(prices[near] - prices[far])
            wanted = (near, far, expected_points)
            if v.get('status') != 'ok' or (v.get('near_contract'),v.get('far_contract')) != wanted[:2] or not math.isclose(v.get('points',float('nan')),expected_points,abs_tol=1e-10):
                failures.append({'kind':'real_official_pair', 'product':p,'date':ds,'expected':wanted,'actual':v})
            counts['real_pairs'] += 1
        counts['real_product_days'] += 1

# Helper allows missing front listing without upstream guard; production prevents this.
p='IM'; day=date(2026,10,15)
f=pd.DataFrame({'instrument':['IM2611','IM2612','IM2703'], 'lastprice':[6075.,6000.,5900.]})
f.attrs.update(source_date=str(day),source='deliberately incomplete standalone frame')
helper_missing_front = target.monthly_spreads(p,f,day,ex)
limitations.append({'kind':'helper_requires_production_listing_guard',
    'input':f.instrument.tolist(),'date':str(day),'actual':helper_missing_front,
    'production_guard':'_validate_complete_future_structure rejects missing IM2610 before helper call',
    'classification':'hardening opportunity only, not reachable production defect'})
limitations.append({'kind':'future_calendar_coverage','covered_official_years':sorted(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS),
    'note':'2027 expiries use weekday fallback already disclosed by _calendar_coverage_note; no verified 2027 closure oracle available; not claiming future holiday accuracy'})
result={'counts':counts,'failures':failures,'boundary_examples':boundaries,'fixtures':fixtures,'limitations':limitations,
        'official_rule':'https://www.cffex.com.cn/u/cms/www/202003/27165856gzzt.pdf',
        'official_2026_june_closure':'https://www.sse.com.cn/disclosure/announcement/general/c/c_20260611_10821419.shtml'}
(OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'counts':counts,'failures':failures,'fixture_summary':fixtures},ensure_ascii=True,indent=2))
raise SystemExit(bool(failures))
