"""Official historical-calendar repair: true session/expiry oracles and gates."""
import ast
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / 'tests/fixtures/icim_historical_calendar_20261022'
NOTICES = json.loads((FIXTURE / 'calendar_notices.json').read_text(encoding='utf-8'))


def oracle():
    return json.loads((FIXTURE / 'cffex_sessions_and_expiries.json').read_text(encoding='utf-8'))


def notice_sessions(year):
    closed = set()
    for start, end in NOTICES['years'][str(year)]['closed_ranges']:
        day, last = date.fromisoformat(start), date.fromisoformat(end)
        while day <= last:
            closed.add(day)
            day += timedelta(days=1)
    days = pd.date_range(f'{year}-01-01', f'{year}-12-31').date
    return [d for d in days if d.weekday() < 5 and d not in closed]


@pytest.mark.parametrize('year', range(2022, 2026))
def test_official_notices_match_complete_cffex_observed_sessions(year):
    expected = [str(d) for d in notice_sessions(year)]
    assert expected == oracle()['session_dates'][str(year)]


@pytest.mark.parametrize('year', range(2022, 2026))
def test_calendar_covers_all_days_of_each_verified_year(year):
    expected = set(notice_sessions(year))
    for d in pd.date_range(f'{year}-01-01', f'{year}-12-31').date:
        bot._require_official_exchange_calendar(d, 'historical oracle')
        assert bot._is_exchange_trading_day(d) is (d in expected)


@pytest.mark.parametrize('year,month', [(y, m) for y in range(2022, 2026) for m in range(1, 13)])
def test_future_expiry_matches_actual_last_trading_date(year, month):
    contract = f'IC{year % 100:02d}{month:02d}'
    assert str(bot._third_friday(year, month)) == oracle()['contract_last_dates'][contract]


@pytest.mark.parametrize('last,next_day', [
    ('2022-09-09', '2022-09-13'), ('2023-06-21', '2023-06-26'),
    ('2024-02-08', '2024-02-19'), ('2024-09-13', '2024-09-18'),
    ('2025-01-27', '2025-02-05'), ('2025-09-30', '2025-10-09'),
])
def test_strict_next_session_uses_actual_exchange_holidays(last, next_day):
    assert bot._roll_forward_exchange_day(date.fromisoformat(last) + timedelta(days=1), require_official=True) == date.fromisoformat(next_day)


def test_january_2023_sse_expiry_and_february_2024_cffex_expiry_shift():
    assert bot._fourth_wednesday(2023, 1) == date(2023, 1, 30)
    assert bot._third_friday(2024, 2) == date(2024, 2, 19)


def test_quarter_tminus3_counts_real_sessions_across_midautumn_2024():
    state = bot.quarter_roll.roll_state('IC', 'IC2409', ['IC2409', 'IC2412'], date(2024, 9, 13), True,
        lambda c: bot._third_friday(2000 + int(c[2:4]), int(c[4:])), bot._is_exchange_trading_day)
    assert state['roll_execution_date'] == date(2024, 9, 13)
    assert state['core_eod_contract'] == 'IC2412'


@pytest.mark.parametrize('year', [2015, 2021, 2027, 2028])
def test_unverified_years_still_fail_closed(year):
    with pytest.raises(bot.CalendarCoverageError):
        bot._require_official_exchange_calendar(date(year, 1, 4), 'unverified')


def test_all_2026_sessions_unchanged():
    expected = json.loads((FIXTURE / 'original_2026_sessions.json').read_text(encoding='utf-8'))
    actual = [str(d) for d in pd.date_range('2026-01-01', '2026-12-31').date if bot._is_exchange_trading_day(d)]
    assert actual == expected


def test_actual_verified_year_boundary_remains_usable_in_both_directions():
    assert bot._roll_backward_exchange_day(date(2026, 1, 1), require_official=True) == date(2025, 12, 31)
    assert bot._roll_forward_exchange_day(date(2026, 1, 1), require_official=True) == date(2026, 1, 5)


@pytest.mark.parametrize('product,day,contract', [('IC', date(2022, 9, 19), 'IC2210'), ('IM', date(2022, 7, 22), 'IM2208')])
def test_historical_first_day_selectors_work_on_actual_quotes(product, day, contract):
    path = (ROOT / 'data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv' if product == 'IC' else
            ROOT / 'data/im_roll_momentum_blend_v1/upstream_im_roll_data/cffex_im_contracts.csv')
    raw = pd.read_csv(path, parse_dates=['date'])
    quotes = raw.loc[raw.date.eq(pd.Timestamp(day))].rename(columns={'contract': 'instrument', 'close': 'lastprice'})
    assert bot.select_active_future(product, quotes, day).instrument == contract


def test_core_strategy_math_did_not_change():
    backup = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/backup/poe_ic_im_mainline_v1_4_bot.py'
    def functions(p):
        tree = ast.parse(p.read_text(encoding='utf-8-sig'))
        return {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, ast.FunctionDef)}
    old, new = functions(backup), functions(ROOT / 'poe_ic_im_mainline_v1_4_bot.py')
    for name in ['_bs_price_delta', '_implied_volatility', 'valuation_score', 'ic_targets', 'im_targets', 'v13_momentum_schedule', '_mom120_floor_state']:
        assert old[name] == new[name], name
