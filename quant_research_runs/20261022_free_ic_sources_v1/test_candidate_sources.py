"""Meaningful rejection tests derived from the actual 2026-10-22 packets."""
from copy import deepcopy
from datetime import date, datetime, time
import json
from pathlib import Path

import pytest
import candidate_sources as c
from validate_candidates import inputs

HERE = Path(__file__).resolve().parent
DAY = date(2026, 10, 22)
CLOCK = datetime.combine(DAY, time(23, 59), c.BEIJING)


def chain(master=None, batches=None, listing=None):
    a, b, d, _ = inputs()
    return c.option_chain(master or a, listing or b, batches or d, DAY, CLOCK)


def test_actual_chain_and_exact_offline_reparse():
    rows = chain()
    assert len(rows) == 29
    assert rows == chain()
    assert sum(r['nonstandard_contract_blocked'] for r in rows) == 14
    assert all(not r['research_selected_quote_eligible'] for r in rows if r['nonstandard_contract_blocked'])
    assert all(not r['research_selected_quote_eligible'] for r in rows if r['source_stamp'].hour == 0)
    held = next(r for r in rows if r['security_id'] == '10012149')
    assert held['contract'] == '510500P2612M06500'
    assert held['unit'] == 10000 and held['research_selected_quote_eligible']
    assert held['last'] < held['bid']  # A valid last need not be inside the current book.
    assert held['volume'] == 8806  # CON_OP[41], never [40] (the session low).


@pytest.mark.parametrize('bad', [True, False, 'NaN', 'Infinity', '-1', None, ''])
def test_invalid_numbers_rejected(bad):
    with pytest.raises(ValueError):
        c.number(bad)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong_id', 'wrong_underlying', 'wrong_expiry', 'wrong_strike', 'wrong_day', 'future', 'empty'])
def test_corrupt_batch_rejected(change):
    _, _, batches, _ = inputs()
    batches = deepcopy(batches)
    ids, raw = batches[0]
    text = raw.decode('gb18030')
    symbol = '10012149'
    import re
    pattern = r'(hq_str_CON_OP_' + symbol + r'=")([^"]*)(")'
    match = re.search(pattern, text)
    if change == 'missing':
        batches.pop()
    elif change == 'duplicate':
        batches.append(batches[0])
    elif change == 'wrong_id':
        batches[0] = (ids, text.replace(symbol, '99999999').encode('gb18030'))
    else:
        f = match[2].split(',')
        updates = {'wrong_underlying': (36, '510300'), 'wrong_expiry': (46, '2026-12-24'),
                   'wrong_strike': (7, '6.75'), 'wrong_day': (32, '2026-10-21 14:56:48'),
                   'future': (32, '2026-10-23 14:56:48')}
        if change == 'empty':
            value = ''
        else:
            index, value = updates[change]
            f[index] = value
            value = ','.join(f)
        batches[0] = (ids, (text[:match.start(2)] + value + text[match.end(2):]).encode('gb18030'))
    with pytest.raises(ValueError):
        chain(batches=batches)


@pytest.mark.parametrize('field,value', [('CONTRACT_UNIT', '10180'), ('TIMESAVE', '2026-10-23'),
    ('START_DATE', '20261023'), ('SECURITYNAMEBYID', '300ETF(510300)')])
def test_invalid_master_terms_rejected(field, value):
    master, _, _, _ = inputs()
    data = json.loads(master)
    next(r for r in data['result'] if r['SECURITY_ID'] == '10012149')[field] = value
    with pytest.raises(ValueError):
        chain(master=json.dumps(data, ensure_ascii=False).encode('utf-8'))


def test_prior_master_price_is_never_used_as_today():
    master, _, _, _ = inputs()
    data = json.loads(master)
    for row in data['result']:
        row['SECURITY_CLOSEPX'] = '99999'
        row['UNDERLYING_CLOSEPX'] = '99999'
    assert chain(master=json.dumps(data, ensure_ascii=False).encode('utf-8')) == chain()


def test_etf_prices_crosscheck_without_rewriting_vendor_time():
    a = c.tencent_etf((HERE / 'probe_02/raw/tencent_etf.bin').read_bytes(), DAY, CLOCK)
    b = c.sina_etf((HERE / 'probe_02/raw/sina_etf.bin').read_bytes(), DAY, CLOCK)
    assert a['last'] == b['last'] == c.number('6.684')
    assert a['previous_close'] == b['previous_close'] == c.number('6.864')
    assert a['source_stamp'].time() == time(16, 14, 51)
    assert b['source_stamp'].time() == time(15, 34, 59)


@pytest.mark.parametrize('bad,replacement', [('510500', '510300'), ('20261022161451', ''),
    ('20261022161451', '20261021161451'), ('20261022161451', '20261022000000'), ('6.684', 'NaN')])
def test_bad_tencent_quote_rejected(bad, replacement):
    raw = (HERE / 'probe_02/raw/tencent_etf.bin').read_bytes().replace(bad.encode(), replacement.encode())
    with pytest.raises(ValueError):
        c.tencent_etf(raw, DAY, CLOCK)


def test_encoded_commas_are_not_accepted_as_batch_success():
    raw = (HERE / 'probe_02/raw/sina_dec_put_batch_0.bin').read_bytes()
    with pytest.raises(ValueError):
        c.variables(raw, 'hq_str_CON_OP_')
