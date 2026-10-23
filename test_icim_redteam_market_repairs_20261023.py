"""Strict index identity/time boundaries, with saved real OHLCV consumption.

Quote packets and clocks below are explicit fault injections. Only the history
fixture is original market data; these tests do not claim fresh provider data.
"""
from datetime import datetime, timedelta
from pathlib import Path
import json

import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot


ROOT = Path(__file__).resolve().parent
CLOCK = datetime(2026, 10, 22, 14, 30, tzinfo=bot.BEIJING)
REAL_RAW = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle/blobs/7155726823743f87af08fae1ce90ef44b6565ef8589cb71199194f8540e42453.bin'


def qt(product='IC', *, symbol=None, code=None, price=7435.164, stamp=None):
    fields = [''] * 31
    fields[0:4] = ['1', 'synthetic-index', code or bot.TENCENT_SYMBOLS[product][2:], str(price)]
    fields[30] = (stamp or CLOCK).strftime('%Y%m%d%H%M%S')
    return ('v_' + (symbol or bot.TENCENT_SYMBOLS[product]) + '="' + '~'.join(fields) + '";').encode('gbk')


def em_quote(product='IC', *, stamp=None):
    return {'f43': 743516.4, 'f57': bot.TENCENT_SYMBOLS[product][2:],
            'f58': 'synthetic-index', 'f86': (stamp or CLOCK).timestamp()}


def response(raw=None, payload=None):
    result = requests.Response()
    result.status_code = 200
    result.encoding = 'gbk'
    result._content_consumed = True
    result._content = raw if raw is not None else json.dumps(payload).encode('utf-8')
    return result


def vendor(monkeypatch, packet=None, em=None, *, history_today=False):
    raw_history = json.loads(REAL_RAW.read_bytes().decode('gbk'))
    history = [r for r in raw_history if r['day'][:10] <= ('2026-10-22' if history_today else '2026-10-21')]
    calls = []

    def get(url, **kw):
        calls.append((url, kw))
        if 'CN_MarketData' in url:
            return response(payload=history)
        if 'qt.gtimg.cn' in url:
            if packet is None:
                raise requests.Timeout('synthetic Tencent outage')
            return response(raw=packet)
        if 'push2.eastmoney.com' in url:
            if em is None:
                raise requests.Timeout('synthetic EastMoney outage')
            return response(payload={'data': em})
        raise AssertionError('Network forbidden: ' + url)

    monkeypatch.setattr(bot, '_evidence_http_get', get)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, '_resolve_gov10y_evidence', lambda d, c, rate, anchor: {
        'yield_decimal': rate, 'source': 'isolated frozen rate'})
    monkeypatch.setattr(bot.daily_valuation, 'resolve', lambda p, d, c, price, frozen, anchor_day: {
        'pe': float(frozen['pe']) * price / float(frozen['price']),
        'pb': float(frozen['pb']) * price / float(frozen['price']),
        'provenance': {'mode': 'proxy', 'reason': 'isolated frozen proxy'}})
    return calls


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('external network forbidden'))


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('provider', ['qt', 'em'])
def test_exact_index_identity_and_timestamp_accepted(monkeypatch, product, provider):
    calls = vendor(monkeypatch, qt(product) if provider == 'qt' else None,
                   em_quote(product) if provider == 'em' else None)
    out = bot.fetch_live_price_quote(product)
    assert out['price'] == pytest.approx(7435.164)
    assert out['source_date'] == CLOCK.date()
    assert out['source_time'] == '14:30:00'
    if provider == 'em':
        params = next(kw['params'] for url, kw in calls if 'push2.eastmoney.com' in url)
        assert {'f57', 'f58'} <= set(params['fields'].split(','))


@pytest.mark.parametrize('mutation', [
    'wrong_variable', 'wrong_body', 'prefix', 'unicode_prefix', 'duplicate_same',
    'duplicate_conflict', 'extra_wrong_variable', 'short_row', 'bad_stamp',
])
@pytest.mark.parametrize('fallback', [False, True])
def test_invalid_tencent_identity_or_structure_falls_back_or_rejects(monkeypatch, mutation, fallback):
    packet = qt()
    if mutation == 'wrong_variable':
        packet = qt(symbol='sh000852')
    elif mutation == 'wrong_body':
        packet = qt(code='000852')
    elif mutation == 'prefix':
        packet = b'x' + packet
    elif mutation == 'unicode_prefix':
        packet = '前'.encode('gbk') + packet
    elif mutation == 'duplicate_same':
        packet += packet
    elif mutation == 'duplicate_conflict':
        packet += qt(price=3000.)
    elif mutation == 'extra_wrong_variable':
        packet += qt(symbol='sh000852', code='000852')
    elif mutation == 'short_row':
        packet = b'v_sh000905="1~index~000905~7435.164";'
    elif mutation == 'bad_stamp':
        packet = packet.replace(b'20261022143000', b'2026102214300')
    vendor(monkeypatch, packet, em_quote() if fallback else None)
    if fallback:
        assert bot.fetch_live_price_quote('IC')['source'] == '东方财富'
    else:
        with pytest.raises(RuntimeError):
            bot.fetch_live_price_quote('IC')


@pytest.mark.parametrize('identity', [None, '000852', 905, '905', '', '000905 '])
def test_eastmoney_missing_or_wrong_identity_rejected(monkeypatch, identity):
    payload = em_quote()
    payload['f57'] = identity
    vendor(monkeypatch, em=payload)
    with pytest.raises(RuntimeError, match='标的身份'):
        bot.fetch_live_price_quote('IC')


@pytest.mark.parametrize('field,value', [
    ('f43', True), ('f43', None), ('f43', float('nan')), ('f43', float('inf')),
    ('f43', 0), ('f43', -1), ('f43', []), ('f86', True), ('f86', None),
    ('f86', float('nan')), ('f86', float('inf')), ('f86', []),
])
def test_eastmoney_invalid_numeric_payload_rejected(monkeypatch, field, value):
    payload = em_quote()
    payload[field] = value
    vendor(monkeypatch, em=payload)
    with pytest.raises(RuntimeError):
        bot.fetch_live_price_quote('IC')


@pytest.mark.parametrize('payload', [None, [], {'data': []}, {'data': 'wrong'}, {}])
def test_eastmoney_malformed_payload_rejected(monkeypatch, payload):
    vendor(monkeypatch)
    monkeypatch.setattr(bot, '_request_json', lambda *a, **kw: payload)
    with pytest.raises(RuntimeError, match='标的身份'):
        bot.fetch_live_price_quote('IC')


def test_index_quote_cancellation_does_not_fall_back(monkeypatch):
    vendor(monkeypatch)
    monkeypatch.setattr(bot, '_evidence_http_get', lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(bot, '_request_json', lambda *a, **kw: pytest.fail('cancellation fell back'))
    with pytest.raises(KeyboardInterrupt):
        bot.fetch_live_price_quote('IC')


@pytest.mark.parametrize('provider', ['qt', 'em'])
@pytest.mark.parametrize('stamp', [
    CLOCK + timedelta(seconds=1), CLOCK.replace(hour=15, minute=0),
    CLOCK.replace(hour=0, minute=0), CLOCK.replace(hour=9, minute=14, second=59),
    CLOCK - timedelta(days=1), CLOCK + timedelta(days=1),
])
def test_invalid_index_time_rejected_before_return(monkeypatch, provider, stamp):
    vendor(monkeypatch, qt(stamp=stamp) if provider == 'qt' else None,
           em_quote(stamp=stamp) if provider == 'em' else None)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: 100.)
    with pytest.raises(RuntimeError):
        bot.fetch_live_price_quote('IC')


def test_invalid_tencent_time_uses_valid_secondary(monkeypatch):
    vendor(monkeypatch, qt(stamp=CLOCK + timedelta(minutes=30)), em_quote())
    assert bot.fetch_live_price_quote('IC')['source'] == '东方财富'


@pytest.mark.parametrize('provider', ['qt', 'em'])
@pytest.mark.parametrize('stamp', [CLOCK, CLOCK.replace(hour=11, minute=30), CLOCK.replace(hour=9, minute=15)])
def test_exact_clock_and_earlier_session_quotes_remain_accepted(monkeypatch, provider, stamp):
    vendor(monkeypatch, qt(stamp=stamp) if provider == 'qt' else None,
           em_quote(stamp=stamp) if provider == 'em' else None)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: 100.)
    assert bot.fetch_live_price_quote('IC')['source_time'] == stamp.strftime('%H:%M:%S')


@pytest.mark.parametrize('provider', ['qt', 'em'])
def test_collection_clock_accepts_timestamp_received_after_start(monkeypatch, provider):
    stamp = CLOCK + timedelta(seconds=1)
    vendor(monkeypatch, qt(stamp=stamp) if provider == 'qt' else None,
           em_quote(stamp=stamp) if provider == 'em' else None)
    tick = [100.]
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    with bot.collection_clock(CLOCK):
        tick[0] = 102.
        assert bot.fetch_live_price_quote('IC')['source_time'] == '14:30:01'


def test_historical_replay_clock_never_advances(monkeypatch):
    vendor(monkeypatch, qt(stamp=CLOCK + timedelta(seconds=1)))
    tick = [100.]
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    with bot.collection_clock(CLOCK), bot.historical_replay(CLOCK.date()):
        tick[0] = 200.
        with pytest.raises(RuntimeError):
            bot.fetch_live_price_quote('IC')


@pytest.mark.parametrize('history_today', [False, True])
@pytest.mark.parametrize('fault', ['future', 'midnight', 'missing_time', 'wrong_identity'])
def test_real_history_consumer_rejects_bad_quote_on_both_merge_paths(monkeypatch, history_today, fault):
    vendor(monkeypatch, qt(), history_today=history_today)
    if fault == 'wrong_identity':
        vendor(monkeypatch, qt(symbol='sh000852', code='000852', price=3000.), history_today=history_today)
    else:
        quote = {'price': 7435.164, 'source': 'injected cached quote', 'source_date': CLOCK.date(),
                 'source_time': {'future': '15:00:00', 'midnight': '00:00:00', 'missing_time': None}[fault]}
        monkeypatch.setattr(bot, 'fetch_live_price_quote', lambda _: quote)
    with pytest.raises(RuntimeError):
        bot.live_proxy('IC', CLOCK)


@pytest.mark.parametrize('history_today', [False, True])
def test_real_history_consumer_retains_valid_quote_and_momentum(monkeypatch, history_today):
    vendor(monkeypatch, qt(), history_today=history_today)
    out = bot.live_proxy('IC', CLOCK)
    assert out['price'] == pytest.approx(7435.164)
    assert out['live_price_source_time'] == '14:30:00'
    assert out['momentum_120'] == pytest.approx(-0.10367951034856637)


def test_live_proxy_explicit_clock_governs_provider_even_without_outer_override(monkeypatch):
    vendor(monkeypatch, qt())
    monkeypatch.setattr(bot, '_now_beijing', lambda: bot._RUNTIME_CLOCK.get() or CLOCK + timedelta(days=1))
    assert bot.live_proxy('IC', CLOCK)['price'] == pytest.approx(7435.164)


def test_completed_replay_does_not_consume_live_price(monkeypatch):
    vendor(monkeypatch, qt(), history_today=True)
    monkeypatch.setattr(bot, 'fetch_live_price_quote', lambda _: pytest.fail('historical replay fetched live tick'))
    with bot.historical_replay(CLOCK.date()):
        assert bot.live_proxy('IC', CLOCK.replace(hour=18))['live_price_source_time'] == '15:00:00'


@pytest.mark.parametrize('clock,stamp', [
    (CLOCK.replace(hour=9, minute=25), CLOCK.replace(hour=9, minute=25)),
    (CLOCK.replace(hour=12, minute=0), CLOCK.replace(hour=11, minute=30)),
])
def test_auction_and_lunch_live_proxy_keep_valid_observations(monkeypatch, clock, stamp):
    vendor(monkeypatch, qt(stamp=stamp))
    monkeypatch.setattr(bot, '_now_beijing', lambda: bot._RUNTIME_CLOCK.get() or clock)
    assert bot.live_proxy('IC', clock)['live_price_source_time'] == stamp.strftime('%H:%M:%S')


def test_preclose_query_can_finish_after_close_with_received_valid_quote(monkeypatch):
    start = CLOCK.replace(hour=14, minute=59, second=59)
    stamp = CLOCK.replace(hour=15, minute=0)
    vendor(monkeypatch, qt(stamp=stamp))
    monkeypatch.setattr(bot, '_now_beijing', lambda: bot._RUNTIME_CLOCK.get() or start)
    tick = [100.]
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    original = bot._evidence_http_get

    def receive(url, **kwargs):
        if 'qt.gtimg.cn' in url:
            tick[0] = 102.
        return original(url, **kwargs)

    monkeypatch.setattr(bot, '_evidence_http_get', receive)
    assert bot.live_proxy('IC', start)['live_price_source_time'] == '15:00:00'
