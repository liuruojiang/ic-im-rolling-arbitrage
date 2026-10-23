"""Independent source audit: actual packets and explicitly synthetic fault cases.

No network, production ledger or original frozen artifacts are written.
"""
from datetime import date, datetime, timedelta
from contextlib import ExitStack
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
SAVED = ROOT / 'quant_research_runs/20261022_free_ic_sources_v1'
DAY = date(2026, 10, 22)


def fail_transport(*args, **kwargs):
    raise requests.Timeout('synthetic source outage')


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('external network forbidden'))


def etf_at(stamp):
    return dict(date='20261022', time=stamp, last=6.684, prev_close=6.864)


@pytest.mark.parametrize('seconds,accepted', [(1, True), (3, False)])
@pytest.mark.parametrize('recording', [False, True])
def test_collection_clock_allows_only_observed_elapsed_time(monkeypatch, tmp_path, seconds, accepted, recording):
    """A vendor refresh during acquisition is not a future observation."""
    clock = datetime(2026, 10, 22, 10, tzinfo=bot.BEIJING)
    elapsed = [100.0]
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(bot, 'fetch_sse_510500_quote', fail_transport)
    monkeypatch.setattr(bot, 'fetch_sina_510500_quote', fail_transport)
    stamp = (clock + timedelta(seconds=seconds)).strftime('%H%M%S')
    monkeypatch.setattr(bot, 'fetch_tencent_510500_quote', lambda: etf_at(stamp))
    with ExitStack() as stack:
        stack.enter_context(bot.runtime_clock(clock))
        stack.enter_context(bot.collection_clock(clock))
        if recording:
            stack.enter_context(evidence.capture_session(tmp_path, DAY, clock, {'test': 'elapsed ETF acquisition'}))
        elapsed[0] += 2.0
        if accepted:
            value = bot.fetch_510500_quote_with_failover(DAY)
            assert value['time'] == stamp
        else:
            with pytest.raises(RuntimeError):
                bot.fetch_510500_quote_with_failover(DAY)


@pytest.mark.parametrize('invalid', ['192612', 'x2612', '20262612'])
def test_invalid_month_cannot_alias_a_valid_cached_chain(monkeypatch, invalid):
    def valid_provider(month):
        if month != '2612':
            raise ValueError('invalid month must reach ordinary provider validation')
        return pd.DataFrame([dict(last=.2614)]), dict(date='20261022', time='150000')
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', valid_provider)
    monkeypatch.setattr(bot, '_validate_chain_stamp_matches', lambda *a: None)
    token = bot._IC_QUOTE_QUERY_CACHE.set({})
    try:
        bot._fetch_510500_chain_for_day('2612', DAY)
        with pytest.raises(ValueError):
            bot._fetch_510500_chain_for_day(invalid, DAY)
    finally:
        bot._IC_QUOTE_QUERY_CACHE.reset(token)


@pytest.mark.parametrize('invalid', ['192612', 'x2612', '20262612'])
def test_capture_role_key_cannot_hide_invalid_month(monkeypatch, tmp_path, invalid):
    """Provider-role coalescing also runs before the ordinary function body."""
    def fetch(month):
        if month != '2612':
            raise ValueError('illegal month must not hit a stored role')
        frame = pd.DataFrame([dict(contract='510500P2612M06500', last=.2614, chg_rate=0.,
                                   pre_settle=.1822, strike=6.5, security_id='10012149')])
        return frame, dict(date='20261022', time='150000', source='synthetic_fault_audit')
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', fetch)
    clock = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)
    with bot.runtime_clock(clock), evidence.capture_session(tmp_path, DAY, clock, {'test': 'invalid alias'}) as session:
        bot._fetch_510500_chain_for_day('2612', DAY)
        with pytest.raises(ValueError):
            bot._fetch_510500_chain_for_day(invalid, DAY)


def listing_response(months):
    # Mutate the saved real response's calendar fields; no synthetic price.
    data = json.loads((SAVED / 'probe_02/raw/sina_listed_months.bin').read_bytes())
    data['result']['data']['contractMonth'] = [months[0], *months]
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(data).encode('utf-8')
    result._content_consumed = True
    return result


def test_old_but_four_month_complete_listing_cannot_change_selection(monkeypatch):
    clock = datetime(2026, 11, 20, 18, tzinfo=bot.BEIJING)
    # A stale September listing can omit March while December still quotes.
    # Target Feb-20 would then incorrectly choose Dec rather than Mar.
    old = ['2026-09', '2026-10', '2026-11', '2026-12']
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: listing_response(old))
    with bot.runtime_clock(clock), pytest.raises(RuntimeError):
        bot.fetch_sina_510500_expiries()


@pytest.mark.parametrize('day,months', [
    (date(2026, 10, 28), ['2026-10', '2026-11', '2026-12', '2027-03']),
    (date(2026, 10, 29), ['2026-11', '2026-12', '2027-03', '2027-06']),
    (date(2026, 11, 20), ['2026-11', '2026-12', '2027-03', '2027-06']),
])
def test_etf_listing_shape_uses_actual_expiry_not_future_maintenance(monkeypatch, day, months):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: listing_response(months))
    clock = datetime.combine(day, datetime.min.time()).replace(hour=18, tzinfo=bot.BEIJING)
    with bot.runtime_clock(clock):
        assert bot.fetch_sina_510500_expiries() == [v.replace('-', '') for v in months]
