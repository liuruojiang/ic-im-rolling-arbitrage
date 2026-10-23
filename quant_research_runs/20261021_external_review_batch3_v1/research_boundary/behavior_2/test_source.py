"""Third-batch research/formal boundary checks; no research engine is executed."""
import ast
from copy import deepcopy
from datetime import date,datetime
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_v1_4_policy as policy
import ic_im_option_maintenance_notice as notice

ROOT = Path(__file__).resolve().parent
HERE = ROOT/'quant_research_runs/20261021_external_review_batch3_v1/research_boundary'
RESEARCH = ROOT/'quant_research_runs/20261021_ic_wait_etf_listing_v1'
SPEC = ROOT/'docs/ic_put_wait_etf_listing_20261021_v1_spec.md'
FORMAL = ['poe_ic_im_mainline_v1_4_bot.py','ic_im_v1_4_policy.py',
          'ic_im_put_execution_v1.py','ic_im_option_maintenance_notice.py',
          'poe_ic_im_v1_4_state.py','poe_ic_im_v1_4_server.py','run_ic_im_v1_4_github_digest.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope='module',autouse=True)
def immutable_binding():
    phase = os.environ.get('ICIM_AUDIT_STAGE','research_boundary/manual').split('/')[-1]
    target = HERE/phase
    target.mkdir(parents=True,exist_ok=True)
    paths = [ROOT/name for name in FORMAL]+[Path(__file__),SPEC,RESEARCH/'record.md',RESEARCH/'completed.json']
    def hashes():return {p.relative_to(ROOT).as_posix():sha(p) for p in paths}
    before = hashes()
    with (target/'source_before.json').open('x',encoding='utf-8') as handle:
        json.dump(before,handle,indent=2)
    yield
    after = hashes()
    with (target/'source_after.json').open('x',encoding='utf-8') as handle:
        json.dump(after,handle,indent=2)
    assert before == after


def production_import_closure():
    """Inspect all local imports, including runtime function-local imports."""
    queue = [ROOT/name for name in FORMAL]
    seen,imports,literals = set(),set(),[]
    while queue:
        path = queue.pop()
        if path in seen:continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node,ast.Import):names = [a.name for a in node.names]
            if isinstance(node,ast.ImportFrom) and node.module:names = [node.module]
            for name in names:
                imports.add(name)
                candidate = ROOT/(name.split('.')[0]+'.py')
                if candidate.is_file():queue.append(candidate)
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                literals.append((path.relative_to(ROOT).as_posix(),node.value))
    return seen,imports,literals


def test_candidate_code_and_outputs_are_not_production_dependencies():
    seen,imports,literals = production_import_closure()
    candidate_modules = {p.stem for p in RESEARCH.glob('*.py')}
    assert not {name for name in imports if name.split('.')[-1] in candidate_modules}
    assert not any(RESEARCH in path.parents for path in seen)
    candidate_paths = ['20261021_ic_wait_etf_listing_v1','20261021_put_monday_settlement_frozen_v1',
                       '20261021_put_monday_close_v1','ic_put_wait_etf_listing_20261021']
    assert not [(path,value) for path,value in literals if any(token in value for token in candidate_paths)]


@pytest.mark.parametrize('month,expected',[ (10,date(2026,10,16)),(11,date(2026,11,20)),(12,date(2026,12,18)) ])
def test_formal_monthly_node_is_not_deferred_until_a_new_etf_month(month,expected,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('calendar event must not consult candidate month-listing logic')
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',forbidden)
    monthly = bot._third_friday(2026,month)
    assert monthly == expected
    due,preview = bot._v14_monthly_option_calendar('IC',monthly,monthly,True)
    assert due and not preview


def test_formal_ic_preceding_close_is_only_a_calendar_preview():
    due,preview = bot._v14_monthly_option_calendar('IC',date(2026,10,15),date(2026,10,16),True)
    assert not due and preview


def test_formal_nearest_three_month_target_has_not_become_minimum_three_months(monkeypatch):
    # Synthetic listed market on a formal maintenance day. September is nearer
    # the October target than December, but below a strict three-month minimum.
    day = date(2026,7,17)
    clock = datetime(2026,7,17,15,40,tzinfo=bot.BEIJING)
    calls = []
    monkeypatch.setattr(bot,'_now_beijing',lambda:clock)
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202609','202612'])
    def chain(month,reference):
        calls.append((month,reference))
        return pd.DataFrame([dict(contract=f'510500P{month}M07500',security_id='10012609',
                                  strike=7.5,last=.2)]),dict(date='20260717',time='150000',source='synthetic_SSE')
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',chain)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *_:.02)
    selected = bot.select_ic_put_for_reset(day,8.,8000.,.25)
    assert selected['contract'] == '510500P2609M07500'
    assert selected['expiry'] == date(2026,9,23)
    assert selected['expiry'] < (pd.Timestamp(day)+pd.DateOffset(months=3)).date()
    assert calls == [('2609',day)]
    assert selected['qty'] is not None and selected['qty'] > 0


def test_zero_protection_does_not_start_listing_wait_or_invent_a_put(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('zero target cannot acquire option/listing dependencies')
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',forbidden)
    selected = bot.select_ic_put_for_reset(date(2026,10,16),8.,8000.,0.)
    assert selected['contract'] is None and selected['qty'] == 0


def test_formal_selection_reacts_to_current_etf_input_instead_of_a_research_strike_anchor(monkeypatch):
    day = date(2026,7,17)
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,7,17,15,40,tzinfo=bot.BEIJING))
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202609','202612'])
    chain = pd.DataFrame([
        dict(contract='510500P2609M07500',security_id='10012609',strike=7.5,last=.2),
        dict(contract='510500P2609M08500',security_id='10012610',strike=8.5,last=.2)])
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',lambda *_:(chain.copy(),
                        dict(date='20260717',time='150000',source='synthetic_SSE')))
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *_:.02)
    first = bot.select_ic_put_for_reset(day,8.,8000.,.25)
    second = bot.select_ic_put_for_reset(day,9.,8000.,.25)
    assert first['strike'] == 7.5 and second['strike'] == 8.5
    assert first['contract'] != second['contract']


@pytest.mark.parametrize('product',['IC','IM'])
def test_monthly_close_is_not_replaced_by_an_ordinary_next_open_plan(product):
    day = date(2026,10,16)
    anchor = policy.default_extension(product)
    signal = dict(market_date=day,next_trade_date=date(2026,10,19),close_confirmed=True,
                  option_monthly_reset_due=True,v14_route_state='future')
    original = deepcopy(anchor)
    bot._v14_schedule_ordinary_put(product,signal,anchor,None)
    assert signal['v14_ordinary_put_pending'] is None
    assert signal.get('v14_ordinary_put_plan_status') != 'scheduled_t_plus_1_open'
    assert anchor == original


@pytest.mark.parametrize('product',['IC','IM'])
def test_monthly_close_uses_current_target_cost_without_research_information_freeze(product):
    from test_ic_im_put_execution_repairs_20261016 import anchor,signal
    held = anchor(product)
    held.update(v14_core_put_entry_premium=.3,v14_core_put_profit3x_eligible=True)
    current = signal(product,option_monthly_reset_due=True,v14_core_put_mark=1.2,
                     v14_core_put_target_entry_premium=.35)
    original = deepcopy(held)
    out = policy.apply_policy(product,current,held,candidate={'tradable':False})
    assert out['v14_core_put_entry_premium'] == .35
    assert out['v14_core_put_profit3x_eligible']
    assert not out['v14_profit_pending']
    assert out['v14_action'] not in {'CORE_PUT_PROFIT3X_REENTER','WAIT_CORE_PUT_PROFIT3X_REENTER'}
    assert held == original


@pytest.mark.parametrize('product',['IC','IM'])
def test_today_maintenance_reminder_survives_hold_without_waiting_for_listing(product):
    day = date(2026,10,16)
    signal = dict(market_date=day,close_confirmed=False,v14_action='HOLD')
    signal['option_monthly_reset_execution_date' if product=='IC' else 'put_monthly_reset_execution_date'] = day
    result = notice.reminder(product,signal,is_session=bot._option_maintenance_session)
    assert '今日（2026-10-16）' in result
    assert '当天收盘' in result and '不顺延至下一交易日开盘' in result
    assert '不因日历提醒新增仓位' in result


def test_frozen_research_spec_and_its_original_scripts_still_match_completion_receipt():
    completed = json.loads((RESEARCH/'completed.json').read_text(encoding='utf-8'))
    assert sha(SPEC) == completed['spec_sha256']
    for name,digest in completed['sources'].items():
        assert sha(ROOT/Path(name)) == digest
