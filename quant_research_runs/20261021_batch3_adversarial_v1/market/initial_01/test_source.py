"""Third-batch independent market red team: real parsers, synthetic payloads.

Historical date indices come from the existing frozen local CSV via the previous
fixture helper. Prices, volumes and VIP inputs are synthetic, not account facts.
No request leaves the process and no production state is imported directly.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import importlib
import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_daily_valuation as valuation
import test_icim_batch3_valuation_20261021 as vfixture

DAY = date(2026, 10, 21)
PREVIOUS = date(2026, 10, 20)
CLOCK = datetime(2026, 10, 21, 18, tzinfo=bot.BEIJING)


class Response:
    status_code = 200
    url = 'https://synthetic.invalid/ohlcv'

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()
        self.headers = {'Content-Length': str(len(self.content))}
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        self.closed = True


def source_parsers(monkeypatch, product, *, current=False, bad_primary=False):
    """Exercise production Sina/Eastmoney/Tencent parsers, no fetch monkeypatch."""
    state = dict(current=current, bad_primary=bad_primary)
    calls, responses = [], []
    lag = vfixture.full_frame(product, PREVIOUS)
    full = vfixture.full_frame(product, DAY)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, 'TENCENT_OHLCV_PAGE_STEP_YEARS', 50)

    def frame():
        return full if state['current'] else lag

    def get(url, **kwargs):
        calls.append(url)
        f = frame()
        if 'sina.com' in url:
            if state['bad_primary']:
                return Response([{'day': 'not-a-date'}])
            payload = [dict(day=p.date().isoformat(), **{k: float(row[k]) for k in
                ['open', 'high', 'low', 'close', 'volume']}) for p, row in f.iterrows()]
        elif url == bot.TENCENT_OHLCV_HISTORY_URL:
            symbol = bot.TENCENT_SYMBOLS[product]
            payload = dict(code=0, msg='', data={symbol: dict(day=[[
                p.date().isoformat(), row.open, row.close, row.high, row.low,
                row.volume / bot.TENCENT_OHLCV_VOLUME_TO_SHARES]
                for p, row in f.iterrows()])})
        else:
            raise AssertionError('unexpected network route: ' + url)
        response = Response(payload)
        responses.append(response)
        return response

    def request_json(url, params):
        assert 'eastmoney' in url
        calls.append(url)
        f = frame()
        return dict(data=dict(klines=[','.join(map(str, [p.date().isoformat(),
            row.open, row.close, row.high, row.low, row.volume, 0]))
            for p, row in f.iterrows()]))

    monkeypatch.setattr(bot.requests, 'get', get)
    monkeypatch.setattr(bot, '_request_json', request_json)
    return state, calls, responses


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_actual_three_source_aggregation_preserves_daily_wait_status(monkeypatch, product):
    _, calls, _ = source_parsers(monkeypatch, product)
    with pytest.raises(bot.DailyOHLCVNotReady) as caught:
        bot.build_live_trade_signal(product, CLOCK, 'close')
    assert caught.value.status == 'waiting_for_completed_daily_ohlcv'
    assert len(calls) == 3


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_actual_parser_failure_does_not_poison_next_completed_snapshot(monkeypatch, product):
    switch, calls, _ = source_parsers(monkeypatch, product)
    with pytest.raises(RuntimeError):
        bot.fetch_ohlcv_history(product)
    switch['current'] = True
    recovered = bot.fetch_ohlcv_history(product)
    assert recovered.index[-1].date() == DAY
    assert recovered.attrs['source'] == 'Sina'
    assert len(calls) == 4


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_real_bad_primary_good_secondary_keeps_secondary_source(monkeypatch, product):
    _, calls, _ = source_parsers(monkeypatch, product, current=True, bad_primary=True)
    result = bot.fetch_ohlcv_history(product)
    assert result.index[-1].date() == DAY
    assert result.attrs['source'] == 'Eastmoney'
    assert 'Sina:' in result.attrs['source_failures'][0]
    assert len(calls) == 2


def ledger_bytes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*.json')}


@pytest.mark.parametrize('entry', ['execute_query', 'catch_up_once'])
def test_all_real_source_lag_keeps_observer_and_temp_ledger_empty(monkeypatch, tmp_path, entry):
    # API and StateStore remain real, with the import-time singleton isolated.
    monkeypatch.setenv('ICIM_STATE_DIR', str(tmp_path/'server_singleton'))
    monkeypatch.setenv('ICIM_REQUIRE_MIGRATION', '0')
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', None)
    server = importlib.import_module('poe_ic_im_v1_4_server')
    from test_icim_round2_audit_lifecycle_20261021 import recent_store
    store, _ = recent_store(tmp_path/'ledger')
    coordinator = server.LedgerCoordinator(store)
    before = ledger_bytes(store.root)
    # Per-product payloads must have the correct frozen date index. Install the
    # source setup at entry into each live_proxy rather than mock its result.
    original_live_proxy = bot.live_proxy
    def live_proxy(product, clock):
        source_parsers(monkeypatch, product)
        return original_live_proxy(product, clock)
    monkeypatch.setattr(bot, 'live_proxy', live_proxy)
    if entry == 'execute_query':
        text, _, observed = coordinator.execute_query('信号', CLOCK)
        assert observed == {}
    else:
        assert coordinator.catch_up_once(CLOCK) is False
        text = coordinator.last_refresh_diagnostic
        assert coordinator.last_refresh_error
    assert '等待当日完整日线' in text
    assert '新目标尚未确认' in text
    assert ledger_bytes(store.root) == before


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_vip_file_changes_are_resolved_per_call_without_sticky_actual(monkeypatch, tmp_path, product):
    path = tmp_path/'snapshot.json'
    monkeypatch.setenv(valuation.FILE_ENV, str(path))
    monkeypatch.delenv(valuation.ENV, raising=False)
    args = dict(product=product, market_day=DAY, clock=CLOCK, price=7000.,
                frozen=bot.FROZEN[product], anchor_day=bot.DATA_CUTOFF)
    current = vfixture.snapshot()
    path.write_bytes(json.dumps(current).encode())
    first = valuation.resolve(**args)
    assert first['provenance']['mode'] == 'vip_actual'
    path.write_bytes(b'{broken')
    second = valuation.resolve(**args)
    assert second['provenance']['mode'] == 'proxy'
    assert 'snapshot_sha256' not in second['provenance']
    assert first['provenance']['mode'] == 'vip_actual'
    current['indices'][valuation.CODES[product]]['pb_aggregate'] = 1.5
    path.write_bytes(json.dumps(current).encode())
    third = valuation.resolve(**args)
    assert third['pb'] == 1.5 and third['provenance']['mode'] == 'vip_actual'
    assert first['pb'] == 5.
    assert third['provenance']['snapshot_sha256'] != first['provenance']['snapshot_sha256']


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('zone', [bot.BEIJING, timezone.utc])
def test_future_vip_cannot_be_used_then_becomes_actual_at_same_instant(monkeypatch, tmp_path, product, zone):
    payload = vfixture.snapshot()
    payload['fetched_at'] = '2026-10-21T18:01:00+08:00'
    vfixture.valuation_input(monkeypatch, tmp_path, payload)
    args = dict(product=product, market_day=DAY, price=7000., frozen=bot.FROZEN[product],
                anchor_day=bot.DATA_CUTOFF)
    early = valuation.resolve(**args, clock=CLOCK.astimezone(zone))
    later_clock = datetime(2026, 10, 21, 18, 1, tzinfo=bot.BEIJING)
    later = valuation.resolve(**args, clock=later_clock.astimezone(zone))
    assert early['provenance']['mode'] == 'proxy'
    assert later['provenance']['mode'] == 'vip_actual'


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_intraday_then_close_uses_fresh_vip_without_reusing_proxy(monkeypatch, tmp_path, product):
    frame = vfixture.live_sources(monkeypatch, tmp_path, product)
    morning = datetime(2026, 10, 21, 13, 30, tzinfo=bot.BEIJING)
    monkeypatch.setattr(bot, 'fetch_live_price_quote', lambda _: dict(price=7800.,
        source='synthetic same-day intraday', source_date=DAY, source_time='13:30:00'))
    before = bot.live_proxy(product, morning)
    close = bot.live_proxy(product, CLOCK)
    assert before['valuation_provenance']['mode'] == 'proxy'
    assert close['valuation_provenance']['mode'] == 'vip_actual'
    assert close['score'] == 3.5
    assert before['momentum_volume_placeholder'] is True
    assert close['momentum_volume_placeholder'] is False
    assert 'realtime_volume_placeholder' not in frame.columns
