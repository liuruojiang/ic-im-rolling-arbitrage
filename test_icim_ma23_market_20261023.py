"""Independent market audit: offline real packets plus explicit corruptions.

No production ledger or network is used. Regression assertions deliberately
remain strict, so malformed primary sources must yield to a verified fallback.
"""
from datetime import date, datetime
import json
from pathlib import Path

import pytest
import requests

import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
SAVED = ROOT / 'quant_research_runs/20261022_free_ic_sources_v1/probe_02/raw'
DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)


def response(body):
    value = requests.Response()
    value.status_code, value.encoding = 200, 'gb18030'
    value._content, value._content_consumed = body, True
    return value


def bare(name):
    value = getattr(bot, name)
    return getattr(value, '__wrapped__', value)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('unmocked external network'))


def sse_months(values):
    # Advertise a complete explicit projection: this is NOT omitted-pagination
    # compatibility. Only its requested-underlying months are corrupted.
    return {'date': '20261120', 'time': 150000, 'total': len(values), 'begin': 0,
            'end': len(values), 'list': [['510500', value] for value in values]}


@pytest.mark.parametrize('months', [
    ['202607', '202608', '202609', '202612'],
    ['202611', '202612'],
    ['202611', '202612', '202703'],
])
def test_sse_explicit_full_page_cannot_admit_stale_or_partial_listing(monkeypatch, months):
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **k: sse_months(months))
    clock = datetime(2026, 11, 20, 18, tzinfo=bot.BEIJING)
    with bot.runtime_clock(clock), pytest.raises(RuntimeError):
        bot.fetch_sse_510500_expiries()


def test_stale_sse_listing_cannot_bypass_sina_and_change_adopted_reset_expiry(monkeypatch):
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **k: sse_months(
        ['202607', '202608', '202609', '202612']))
    fallback_calls = []
    def current_sina():
        fallback_calls.append(True)
        return ['202611', '202612', '202703', '202706']
    monkeypatch.setattr(bot, 'fetch_sina_510500_expiries', current_sina)
    class SelectedExpiry(Exception):
        pass
    # Observe the real selector immediately BEFORE the adopted-date gate and
    # quote/sizing stages. This test produces no formal signal or position.
    def observe(expiry, purpose):
        raise SelectedExpiry(expiry)
    monkeypatch.setattr(bot, '_require_model_expiry_calendar', observe)
    day = date(2026, 11, 20)
    with bot.runtime_clock(CLOCK.replace(month=11, day=20)), pytest.raises(SelectedExpiry) as caught:
        bot.select_ic_put_for_reset(day, 6.7, 6500., .25)
    assert caught.value.args[0] == date(2027, 3, 24)
    assert fallback_calls == [True]


@pytest.mark.parametrize('vendor,prefix', [('tencent', b'v_sh510500='), ('sina', b'hq_str_sh510500=')])
def test_wrong_identifier_namespace_cannot_be_accepted_as_exact_etf_variable(monkeypatch, vendor, prefix):
    original = (SAVED / (vendor + '_etf.bin')).read_bytes()
    corrupted = original.replace(prefix, b'not_' + prefix, 1)
    assert corrupted != original
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(corrupted))
    with pytest.raises(RuntimeError):
        getattr(bot, 'fetch_' + vendor + '_510500_quote')()


def test_sse_json_source_respects_actual_quote_byte_budget(monkeypatch):
    # Tiny patched limit avoids allocating huge input. Bytes are syntactically
    # valid JSON; status/JSON success alone cannot establish bounded transport.
    monkeypatch.setattr(bot, 'MAX_QUOTE_RESPONSE_BYTES', 32)
    body = b' ' * 40 + b'{"list":[]}'
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with pytest.raises(RuntimeError, match='超过|上限|大小'):
        bot._sse_quote_json('v1/sh1/list/self/510500', {})


@pytest.mark.parametrize('vendor', ['tencent', 'sina'])
@pytest.mark.parametrize('mutation', ['wrong_symbol', 'duplicate'])
def test_saved_etf_packet_rejects_wrong_symbol_and_duplicate_variables(monkeypatch, vendor, mutation):
    original = (SAVED / (vendor + '_etf.bin')).read_bytes()
    body = original.replace(b'sh510500=', b'sh510300=', 1) if mutation == 'wrong_symbol' else original + original
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with pytest.raises(RuntimeError):
        getattr(bot, 'fetch_' + vendor + '_510500_quote')()


def test_two_invalid_primary_sources_cannot_hide_valid_sina_identity(monkeypatch):
    sse = {'date': '20261022', 'time': 150000, 'total': 1, 'begin': 0, 'end': 1,
           'list': [['510500', 'synthetic corrupted price', True, 0, 0, 0, 0, 0, 6.864]]}
    def get(url, **kwargs):
        if 'yunhq.sse.com.cn' in url:
            return response(json.dumps(sse).encode())
        if 'qt.gtimg.cn' in url:
            return response((SAVED / 'tencent_etf.bin').read_bytes().replace(b'~510500~', b'~510300~', 1))
        assert url == 'https://hq.sinajs.cn/'
        return response((SAVED / 'sina_etf.bin').read_bytes())
    monkeypatch.setattr(bot.requests, 'get', get)
    with bot.runtime_clock(CLOCK):
        value = bare('fetch_510500_quote_with_failover')(DAY)
    assert value['source'] == '新浪510500ETF行情' and value['last'] == 6.684
    assert len(value['source_failures']) == 2


@pytest.mark.parametrize('value', [None, '', True, '000000', '091459', '240000', '09:30:00', '１２３４５６'])
def test_sse_session_time_cannot_convert_missing_or_invalid_data_to_real_time(value):
    with pytest.raises(RuntimeError):
        bot._sse_session_time(value, 'adversarial SSE')


@pytest.mark.parametrize('errors,reader,expected', [
    ([requests.Timeout('t'), requests.ConnectionError('c')], False, bot.SignalTransportError),
    ([requests.Timeout('t'), RuntimeError('invalid row')], False, RuntimeError),
    ([requests.exceptions.ChunkedEncodingError('c')], False, bot.SignalTransportError),
    ([evidence.EvidenceUnavailable('missing'), RuntimeError('invalid alternate')], True, evidence.EvidenceUnavailable),
])
def test_aggregate_error_classification_does_not_convert_validation_to_transport(errors, reader, expected):
    token = evidence.current_reader.set(object() if reader else None)
    try:
        value = bot._source_failure_error('offline fault', errors)
    finally:
        evidence.current_reader.reset(token)
    assert type(value) is expected


@pytest.mark.parametrize('url,params,literal', [
    ('https://hq.sinajs.cn/', {'list': 'CON_OP_10000001,CON_OP_10000002'}, True),
    ('https://hq.sinajs.cn/etag.php', {'list': 'P_OP_mo2612P6000,P_OP_mo2612P6100'}, True),
    ('https://hq.sinajs.cn/', {'list': 'a,b', 'extra': 'x'}, False),
    ('https://hq.sinajs.cn.evil.invalid/', {'list': 'a,b'}, False),
    ('https://hq.sinajs.cn/', {'list': 'a,b&outside=1'}, False),
    ('https://hq.sinajs.cn/', {'list': 'a,中'}, False),
])
def test_literal_csv_serialization_stays_inside_exact_safe_wire_scope(monkeypatch, url, params, literal):
    observed = []
    def get(*a, **kwargs):
        observed.append(kwargs['params'])
        return response(b'offline synthetic response')
    monkeypatch.setattr(bot.requests, 'get', get)
    bot._evidence_http_get(url, params=params)
    assert observed == ['list=' + params['list']] if literal else observed == [params]


def test_safe_literal_wire_keeps_logical_evidence_parameters_for_replay(monkeypatch, tmp_path):
    url = 'https://hq.sinajs.cn/etag.php'
    params = {'list': 'P_OP_mo2612P6000,P_OP_mo2612P6100'}
    body = b'var hq_str_P_OP_mo2612P6000="synthetic";var hq_str_P_OP_mo2612P6100="synthetic";'
    seen = []
    def get(*a, **kwargs):
        seen.append(kwargs['params'])
        return response(body)
    monkeypatch.setattr(bot.requests, 'get', get)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {'synthetic': True}) as session:
        session.call('scope', {}, lambda: True)
        assert bot._evidence_http_get(url, params=params).content == body
    assert seen == ['list=' + params['list']]
    assert evidence.decode(session.context['raw'][0]['params']) == params
    reader = evidence.load_session(tmp_path, DAY)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay escaped to network'))
    with evidence.replay_session(reader):
        assert bot._evidence_http_get(url, params=params).content == body
    assert not reader.missing_inputs


def real_mo_packet():
    source = ROOT / 'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin'
    packet = next(value.strip() for value in source.read_bytes().split(b';') if value.strip()) + b';'
    symbol = packet.split(b'=')[0].split(b'hq_str_')[1]
    fields = packet.split(b'=')[1].strip(b';').strip(bytes([34])).decode('gb18030').split(',')
    # The saved wire oracle is an actual 2026-10-22 MO2611-P-6000 quote.
    assert symbol == b'P_OP_mo2611P6000' and fields[32] == '2026-10-22 15:00:00'
    return packet, symbol, float(fields[2])


def test_real_mo_crosscheck_packet_still_accepts_its_requested_identity(monkeypatch):
    packet, _, price = real_mo_packet()
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(packet))
    value = bot.verify_sina_option_quote('MO2611-P-6000', DAY, price, CLOCK)
    assert value['lastprice'] == price and value['contract'] == 'MO2611-P-6000'


@pytest.mark.parametrize('mutation', ['wrong_contract', 'wrong_namespace', 'duplicate'])
def test_mo_crosscheck_rejects_unrequested_or_ambiguous_returned_variable(monkeypatch, mutation):
    packet, symbol, price = real_mo_packet()
    if mutation == 'wrong_contract':
        packet = packet.replace(symbol, b'P_OP_mo2611P6100', 1)
    elif mutation == 'wrong_namespace':
        packet = packet.replace(b'hq_str_', b'not_hq_str_', 1)
    else:
        packet = packet + packet
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(packet))
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000', DAY, price, CLOCK)


def test_mo_last_trade_crosscheck_cannot_accept_same_day_future_quote_time(monkeypatch):
    packet, _, price = real_mo_packet()
    packet = packet.replace(b'2026-10-22 15:00:00', b'2026-10-22 18:01:00', 1)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(packet))
    # Same date is insufficient: ordinary Eastmoney MO cross-check consumes
    # this result and receives no source time with which to reject it later.
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000', DAY, price, CLOCK)
