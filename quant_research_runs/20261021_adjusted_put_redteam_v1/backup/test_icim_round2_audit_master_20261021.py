"""Independent master-data audit; raw official responses, synthetic contradictions.

This module never imports the bot or writes an operational ledger. Diagnostics
remain engine_supported=False; costs/migration are explicitly outside this audit.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest
import ic_im_sse_contract_identity_v1 as identity

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'quant_research_runs/20261021_external_review_round2_v1/raw'
AUDIT = ROOT / 'quant_research_runs/20261021_external_review_round2_redteam_v1/master'
DAY = date(2026, 10, 21)
EFFECTIVE = date(2026, 7, 15)
SECURITY = '10011470'
OLD = '510500P2612M07500'


def load(name):
    return json.loads((RAW / name).read_bytes())


def event_body():
    return load('sse_adjustment_20260715.json')


def events():
    return identity.parse_adjustment_events(event_body(), effective_date=EFFECTIVE)


def stage_dir():
    # runner determines isolated TEMP/state; our files use the same phase only.
    phase = os.environ.get('ICIM_AUDIT_STAGE', 'master/manual').split('/')[-1]
    path = AUDIT / phase
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_new(name, value):
    target = stage_dir() / name
    # First results are immutable. A rerun must choose another stage.
    with target.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)


@pytest.fixture(scope='module', autouse=True)
def binding():
    files = [Path(identity.__file__), Path(__file__), RAW/'sse_adjustment_20260715.json',
             RAW/'sse_december_identity.json', RAW/'sse_contract_master.json',
             ROOT/'quant_research_runs/20261021_external_review_round2_redteam_v1/run_checks.py',
             ROOT/'poe_ic_im_mainline_v1_4_bot.py', ROOT/'ic_im_v1_4_policy.py',
             ROOT/'poe_ic_im_v1_4_state.py', ROOT/'ic_im_put_execution_v1.py']
    def hashes():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    before = hashes()
    save_new('source_before.json', before)
    yield
    after = hashes()
    save_new('source_after.json', after)
    assert before == after, 'audit inputs changed during independent run'


def test_real_raw_source_bytes_match_previously_saved_capture():
    expected = {'sse_adjustment_20260715.json':'85f3f74d8c088e586d7d6275697f104ad5a5774a1e32bc32217db178b5557596',
                'sse_december_identity.json':'327aacd4c032bda5be4535ad6ef2312fe179ea791a293b81944368a0003476b7',
                'sse_contract_master.json':'22ebf371548b9c64f0cdab36bf263aa48ad525c702a4e672d37529b0ba940425'}
    for name, digest in expected.items():
        assert hashlib.sha256((RAW/name).read_bytes()).hexdigest() == digest
        fixture = ROOT/'tests/fixtures/icim_external_review_round2_20261021'/name
        assert fixture.read_bytes() == (RAW/name).read_bytes()


def test_all_114_events_independently_match_raw_identity_dates_units():
    parsed = events()
    original = event_body()['result']
    assert len(parsed) == len(original) == 114
    assert len({r['security_id'] for r in parsed}) == 114
    by_id = {r['SECURITY_ID']:r for r in original}
    for item in parsed:
        raw = by_id[item['security_id']]
        assert item['contract'] == raw['CONTRACT_ID']
        assert item['previous_contract'] == raw['ORG_CONTRACT_ID']
        assert item['contract'][:11]+'M'+item['contract'][12:] == item['previous_contract']
        assert Decimal(str(item['strike'])) == Decimal(raw['EXERCISE_PRICE'])
        assert item['contract_unit'] == int(raw['CONTRACT_UNIT']) == 10180
        assert item['previous_unit'] == int(raw['ORG_CONTRACT_UNIT']) == 10000
        assert item['effective_date'] == EFFECTIVE
        assert date.fromisoformat(raw['START_DATE']) <= EFFECTIVE <= item['expiry']
        assert item['expiry'] == date.fromisoformat(raw['EXPIRE_DATE'])


def test_real_58_quotes_bind_to_actual_master_and_14_put_diagnostics():
    body, parsed = load('sse_december_identity.json'), events()
    raw_master = load('sse_contract_master.json')['result']
    master = {r['SECURITY_ID']:r for r in raw_master}
    assert len(body['list']) == len(raw_master) == len(master) == 58
    results = []
    for row in body['list']:
        raw = master[row[5]]
        assert row[0] == raw['CONTRACT_ID']
        assert Decimal(str(row[4])) == Decimal(raw['EXERCISE_PRICE'])
        assert raw['TIMESAVE'] == '2026-10-20'
        if row[0].startswith('510500P2612A'):
            out = identity.resolve_adjusted_holding(parsed, body, row[5], row[0], DAY)
            assert out['contract_unit'] == int(raw['CONTRACT_UNIT'])
            assert out['expiry'] == date.fromisoformat(raw['EXPIRE_DATE'])
            assert out['engine_supported'] is False
            assert out['observed_on'] == DAY and out['effective_date'] == EFFECTIVE
            results.append(out)
    assert len(results) == 14
    save_new('real_reconciliation.json', dict(events=114, quotes=58, adjusted_puts=results,
             master_data_day='2026-10-20', quote_day=body['date'], quote_time=body['time'],
             limits=['official sources assumed authentic; not broker proof',
                     'master is dated 2026-10-20, quote is dated 2026-10-21',
                     'no costs, ledger migration, or trading authorization']))


@pytest.mark.parametrize('field,value', [('EXERCISE_PRICE','nan'), ('EXERCISE_PRICE','inf'),
    ('EXERCISE_PRICE',True), ('EXERCISE_PRICE',0), ('ORG_EXERCISE_PRICE','7.499'),
    ('CONTRACT_UNIT','NaN'), ('CONTRACT_UNIT','Infinity'), ('CONTRACT_UNIT',False),
    ('CONTRACT_UNIT','10180.00001'), ('CONTRACT_UNIT',-1), ('ORG_CONTRACT_UNIT','10180'),
    ('EXPIRE_MONTH','202611'), ('ADJUST_DATE','20260716'), ('EXERCISE_DATE','20261224'),
    ('END_DATE','20261222'), ('SECURITY_ID',10011470)])
def test_parser_rejects_nonfinite_type_precision_unit_or_interval(field, value):
    body = event_body()
    row = next(r for r in body['result'] if r['SECURITY_ID'] == SECURITY)
    row[field] = value
    with pytest.raises(RuntimeError):
        identity.parse_adjustment_events(body, effective_date=EFFECTIVE)


@pytest.mark.parametrize('mutation', ['duplicate', 'wrong_alias', 'future', 'expired', 'stale_quote'])
def test_resolver_rejects_ambiguity_unproved_alias_or_time(mutation):
    parsed, quote, held, day = events(), load('sse_december_identity.json'), OLD, DAY
    if mutation == 'duplicate':
        parsed.append(deepcopy(next(r for r in parsed if r['security_id'] == SECURITY)))
    elif mutation == 'wrong_alias': held = '510500P2612M07367'
    elif mutation == 'future': day=date(2026,7,14); quote['date']=20260714
    elif mutation == 'expired': day=date(2026,12,24); quote['date']=20261224
    elif mutation == 'stale_quote': quote['date']=20261020
    with pytest.raises(RuntimeError):
        identity.resolve_adjusted_holding(parsed, quote, SECURITY, held, day)


def test_diagnostic_is_read_only_and_retains_guard_for_old_and_new_alias():
    parsed, quote = events(), load('sse_december_identity.json')
    before = deepcopy((parsed, quote))
    for held, alias_required in [(OLD, True), ('510500P2612A07500', False)]:
        result=identity.resolve_adjusted_holding(parsed, quote, SECURITY, held, DAY)
        assert result['engine_supported'] is False
        assert result['migration_required'] is alias_required
        assert result['status'] == 'requires_unit_and_cost_basis_migration'
    assert (parsed, quote) == before


@pytest.mark.parametrize('field,value', [('actionErrors',['official query failed']),
    ('fieldErrors',{'securityCode':['invalid scope']}), ('START_DATE','20260716')])
def test_explicit_response_errors_or_impossible_listing_interval_are_rejected(field, value):
    body = event_body()
    if field == 'START_DATE':
        next(r for r in body['result'] if r['SECURITY_ID'] == SECURITY)[field] = value
    else:
        body[field] = value
    actual = {}
    try:
        result=identity.parse_adjustment_events(body, effective_date=EFFECTIVE)
        actual = dict(returned_count=len(result), target=next(r for r in result if r['security_id']==SECURITY))
    except RuntimeError as error:
        actual = dict(rejected=True, error=str(error))
    save_new('contradiction_'+field+'.json', dict(field=field, injected=value, actual=actual,
             severity='P2 diagnostic integrity; no production A execution',
             reproduction='parse_adjustment_events(real_raw_with_single_field_mutation, effective_date=2026-07-15)',
             trust_boundary='single internal contradiction, not forged consistent authority data'))
    assert actual.get('rejected'), 'explicit source error or listing-after-adjustment must not produce a verified event'


@pytest.mark.parametrize('value', [0, 0.0])
def test_numeric_zero_is_not_a_boolean_false_pagination_marker(value):
    body = event_body()
    body['isPagination'] = value
    try:
        result = identity.parse_adjustment_events(body, effective_date=EFFECTIVE)
        actual = dict(returned_count=len(result))
    except RuntimeError as error:
        actual = dict(rejected=True, error=str(error))
    save_new('pagination_schema_'+type(value).__name__+'.json', dict(value=value,
             python_type=type(value).__name__, actual=actual,
             severity='P3 diagnostic schema ambiguity; no trading execution',
             reproduction='replace real envelope isPagination string false with numeric zero'))
    assert actual.get('rejected'), 'numeric zero must not satisfy an explicit boolean/string pagination schema'
