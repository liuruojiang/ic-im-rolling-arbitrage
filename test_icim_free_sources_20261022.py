"""Free-source integration: real saved packets with explicit offline fault injection."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import re

import pandas as pd
import pytest
import requests

import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
SAVED = ROOT / 'quant_research_runs/20261022_free_ic_sources_v1'
DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('unmocked external request'))


def response(body):
    result = requests.Response()
    result.status_code = 200
    result.encoding = 'gb18030'
    result._content, result._content_consumed = body, True
    return result


def fail(error):
    def fetch(*a, **k):
        raise error
    return fetch


@pytest.mark.parametrize('source', ['tencent', 'sina'])
def test_actual_etf_fields_and_vendor_time(monkeypatch, source):
    body = (SAVED / 'probe_02/raw' / (source + '_etf.bin')).read_bytes()
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    quote = getattr(bot, 'fetch_' + source + '_510500_quote')()
    assert quote['last'] == 6.684 and quote['prev_close'] == 6.864
    assert quote['time'] == ('161451' if source == 'tencent' else '153459')
    assert quote['date'] == '20261022'
    assert quote['source'] == ('腾讯510500ETF行情' if source == 'tencent' else '新浪510500ETF行情')
    # Vendor update times cannot silently become a 15:00 IV monitor observation.
    stamp = datetime.strptime(quote['date'] + quote['time'], '%Y%m%d%H%M%S').replace(tzinfo=bot.BEIJING)
    with pytest.raises(ValueError):
        bot._validate_iv_monitor_times(DAY, stamp, stamp, CLOCK, True)


@pytest.mark.parametrize('target', ['tencent', 'sina'])
def test_alternate_etf_capture_and_reader_same_value(monkeypatch, tmp_path, target):
    body = (SAVED / 'probe_02/raw' / (target + '_etf.bin')).read_bytes()
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        return response(body)
    monkeypatch.setattr(bot.requests, 'get', get)
    monkeypatch.setattr(bot, 'fetch_sse_510500_quote', fail(requests.Timeout('injected SSE outage')))
    if target == 'sina':
        monkeypatch.setattr(bot, 'fetch_tencent_510500_quote', fail(requests.Timeout('injected Tencent outage')))
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {'test': 'alternate ETF'}) as session:
        original = bot.fetch_510500_quote_with_failover(DAY)
    reader = evidence.load_session(tmp_path, DAY, required_roles={'ic.etf'})
    assert session.context['complete'] is True
    assert {r['url'] for r in reader.context['raw']} == set(urls)
    monkeypatch.setattr(bot.requests, 'get', fail(AssertionError('replay must be offline')))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader):
        replay = bot.fetch_510500_quote_with_failover(DAY)
    assert replay == original
    assert reader.missing_inputs == []


@pytest.mark.parametrize('bad_stamp', [('20261021','150000'), ('20261022','000000'), ('20261022','190000'), ('20261022','')])
def test_etf_failover_checks_day_and_clock_before_accepting(monkeypatch, bad_stamp):
    monkeypatch.setattr(bot, 'fetch_sse_510500_quote', lambda: dict(date=bad_stamp[0], time=bad_stamp[1],last=6.684,prev_close=6.864))
    body = (SAVED / 'probe_02/raw/tencent_etf.bin').read_bytes()
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with bot.runtime_clock(CLOCK):
        result = bot.fetch_510500_quote_with_failover(DAY)
    assert result['source'] == '腾讯510500ETF行情'


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '0', '-1'])
def test_invalid_free_etf_price_is_rejected(monkeypatch, bad):
    body = (SAVED / 'probe_02/raw/tencent_etf.bin').read_bytes().replace(b'6.684', bad.encode())
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with pytest.raises(RuntimeError):
        bot.fetch_tencent_510500_quote()


def test_all_transports_remain_retryable(monkeypatch):
    for name in ['fetch_sse_510500_quote','fetch_tencent_510500_quote','fetch_sina_510500_quote']:
        monkeypatch.setattr(bot, name, fail(requests.Timeout('injected')))
    with pytest.raises(bot.SignalTransportError):
        bot.fetch_510500_quote_with_failover(DAY)


def actual_chain_router(monkeypatch, *, transient=False, invalid=False):
    listing = (SAVED / 'probe_01/raw/sina_december_put_ids.bin').read_bytes()
    packets = {}
    for stage in ['probe_03', 'probe_04']:
        for path in (SAVED / stage / 'raw').glob('sina_literal_batch_*.bin'):
            packets.update({sid: match for match in re.findall(rb'hq_str_CON_OP_[0-9]+="[^"\r\n]*"', path.read_bytes())
                            for sid in [re.search(rb'CON_OP_([0-9]+)', match)[1].decode()]})
    calls = []
    seen = set()
    def get(url, **kwargs):
        if url == 'https://hq.sinajs.cn/':
            return response(listing)
        assert '%2C' not in url
        ids = [v.removeprefix('CON_OP_') for v in url.split('list=')[1].split(',')]
        assert len(ids) <= 6
        calls.append(tuple(ids))
        if transient and tuple(ids) not in seen:
            seen.add(tuple(ids))
            raise requests.Timeout('injected first batch transport failure')
        chosen = ids[:-1] if invalid else ids
        return response(b';\n'.join(b'var ' + packets[sid] for sid in chosen) + b';')
    monkeypatch.setattr(bot.requests, 'get', get)
    return calls


def test_actual_29_quotes_split_into_exact_bounded_batches(monkeypatch):
    calls = actual_chain_router(monkeypatch)
    chain, stamp = bot.fetch_sina_510500_chain('2612')
    assert sorted(map(len, calls)) == [5,6,6,6,6]
    assert len(chain) == 15
    assert len(chain.attrs['unresolved_adjusted_contracts']) == 14
    held = chain.loc[chain.security_id == '10012149'].iloc[0]
    assert held['contract'] == '510500P2612M06500' and held['last'] == .2614
    bot._require_selected_510500_quote_time(held, CLOCK, DAY)
    midnight = chain.loc[chain.security_id == '10012105'].iloc[0]
    with pytest.raises(RuntimeError):
        bot._require_selected_510500_quote_time(midnight, CLOCK, DAY)


def test_only_failed_batch_is_retried_once(monkeypatch):
    calls = actual_chain_router(monkeypatch, transient=True)
    chain, _ = bot.fetch_sina_510500_chain('2612')
    assert len(chain) == 15 and len(calls) == 10
    assert all(calls.count(batch) == 2 for batch in set(calls))


def test_missing_batch_identity_fails_without_retry_or_partial_chain(monkeypatch):
    calls = actual_chain_router(monkeypatch, invalid=True)
    with pytest.raises(RuntimeError, match='集合'):
        bot.fetch_sina_510500_chain('2612')
    # Already-running independent batches may finish, but malformed data is
    # never retried and no partial chain is published.
    assert calls and len(calls) == len(set(calls))


@pytest.mark.parametrize('mutation', ['stock', 'status', 'month', 'duplicate', 'partial'])
def test_real_listing_rejects_bad_identity_or_partial_months(monkeypatch, mutation):
    data = json.loads((SAVED / 'probe_02/raw/sina_listed_months.bin').read_bytes())
    result = data['result']
    if mutation == 'stock': result['data']['stockId'] = '510300'
    elif mutation == 'status': result['status']['code'] = True
    elif mutation == 'month': result['data']['contractMonth'][2] = '2026-13'
    elif mutation == 'duplicate': result['data']['contractMonth'][3] = result['data']['contractMonth'][2]
    else: result['data']['contractMonth'].pop()
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(json.dumps(data).encode()))
    with pytest.raises(RuntimeError):
        bot.fetch_sina_510500_expiries()


def test_real_listing_fallback(monkeypatch):
    monkeypatch.setattr(bot, 'fetch_sse_510500_expiries', fail(requests.Timeout('injected')))
    body = (SAVED / 'probe_02/raw/sina_listed_months.bin').read_bytes()
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: response(body))
    with bot.runtime_clock(CLOCK):
        assert bot.fetch_510500_expiries_with_failover() == ['202610','202611','202612','202703']


def test_chain_reuse_has_day_month_reader_isolation_and_deepcopy(monkeypatch):
    calls = []
    def fetch(month):
        calls.append(month)
        return pd.DataFrame([{'last': .2614}]), {'date':'20261022','time':'150000'}
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', fetch)
    monkeypatch.setattr(bot, '_validate_chain_stamp_matches', lambda *a: None)
    token = bot._IC_QUOTE_QUERY_CACHE.set({})
    try:
        value = bot._fetch_510500_chain_for_day('2612', DAY)
        value[0].loc[0, 'last'] = 999
        assert bot._fetch_510500_chain_for_day('2612', DAY)[0].iloc[0]['last'] == .2614
        bot._fetch_510500_chain_for_day('2612', date(2026,10,23))
        bot._fetch_510500_chain_for_day('2712', DAY)
        with bot.historical_replay(DAY):
            bot._fetch_510500_chain_for_day('2612', DAY)
    finally:
        bot._IC_QUOTE_QUERY_CACHE.reset(token)
    bot._fetch_510500_chain_for_day('2612', DAY)
    assert calls == ['2612','2612','2712','2612','2612']
