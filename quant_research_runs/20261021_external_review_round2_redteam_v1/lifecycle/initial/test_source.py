"""Independent round-two lifecycle audit; production state is never opened.

Source fixtures preserve accepted schemas; prices/date shifts are explicitly
synthetic except the saved SSE chain used for identity checks.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_ic_im_external_review_20261021 as seed
import test_ic_im_external_review_round2_20261021 as real

DAY = date(2026, 10, 21)
PREVIOUS = date(2026, 10, 20)
CLOCK = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)


def historical_source(monkeypatch):
    values = pd.Series([.24, .25], index=pd.to_datetime([PREVIOUS, DAY]))
    values.attrs['source'] = 'synthetic_ID_keyed_saved_history'
    calls = []
    def source(security):
        calls.append(security)
        assert security == '10012099'
        return values
    monkeypatch.setattr(bot, 'fetch_option_closes', source)
    return calls


def test_historical_adapter_retains_id_already_bound_to_requested_series(monkeypatch):
    calls = historical_source(monkeypatch)
    frame, stamp = bot.fetch_sse_existing_put_historical_quote(real.M, '10012099', DAY)
    assert calls == ['10012099']
    assert frame.iloc[0].get('security_id') == '10012099'
    assert frame.iloc[0]['last'] == .25


def test_legal_standard_core_replay_crosses_strict_boundary(monkeypatch):
    calls = historical_source(monkeypatch)
    a = ledger._record()['products']['IC']
    signal = dict(market_date=DAY, core_put_target_delta=.25,
                  option_monthly_reset_due=False, put_target_momentum_qty=0,
                  etf_price=7.8, future_last=7800.)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    token = bot._HISTORICAL_REPLAY_DAY.set(DAY)
    try:
        bot._v14_ic_core_overlay(signal, a)
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    assert calls == ['10012099']
    assert signal['iv_monitor_evidence']['security_id'] == '10012099'
    assert signal['iv_monitor_option_price'] == .25


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*.json')}


def recent_store(root):
    record = ledger._record()
    record.update(verified_day=PREVIOUS.isoformat(), signals={})
    for a in record['products'].values():
        a.update(last_verified_day=PREVIOUS.isoformat(), verified_grid_entry_source='none',
                 verified_next_grid_entry_source='none')
    record['digest'] = state._digest(record)
    state._validate_record(record)
    store = state.StateStore(root)
    store._atomic_write(store.journal_dir / '000000-2026-10-20.json', record)
    store._atomic_write(store.latest_path, record)
    return store, store.load_latest()


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('kind', ['ordinary', 'profit3x', 'short_put'])
def test_actual_public_entry_resets_projection_after_later_failure(tmp_path, monkeypatch, product, kind):
    store, record = recent_store(tmp_path / 'ledger')
    anchors = state.anchors_from_record(record)
    if kind == 'ordinary':
        a = seed.ordinary_anchor(product)
    elif kind == 'profit3x':
        a = seed.profit_anchor(product)
    else:
        a = seed.short_anchor(product)
    # The persisted schema is independently accepted before invoking builder.
    policy.validate_extension(product, a)
    anchors[product].update(a)
    record['products'][product] = state._jsonable(anchors[product])
    record['digest'] = state._digest(record)
    state._validate_record(record)
    store._atomic_write(store.journal_dir / '000000-2026-10-20.json', record)
    store._atomic_write(store.latest_path, record)
    record = store.load_latest()
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', state.anchors_from_record(record))
    a = bot.LIVE_CONTINUATION_ANCHOR[product]
    before_anchor, before_disk = deepcopy(a), snapshot(store.root)
    monkeypatch.setattr(bot, 'live_proxy', lambda *args: {'history_date': DAY})
    # Real market-date validation accepts exactly T-1 -> T. This sentinel is
    # reached after actual opening projection and before quote/fear gathering.
    observed = []
    def later_fail(*args, **kwargs):
        observed.append(deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product]))
        raise RuntimeError('independent failure after successful opening projection')
    monkeypatch.setattr(bot, '_apply_next_unverified_session_anchor', later_fail)
    monkeypatch.setattr(bot, 'fetch_option_dated_open', lambda *args: (.35, 'synthetic_dated_open'))
    frame = pd.DataFrame([dict(instrument='MO2612-P-7600', openprice=35.),
                          dict(instrument='MO2701-P-6800', openprice=36.),
                          dict(instrument='MO2611-P-6200', openprice=37.),
                          dict(instrument='IM2612', openprice=7800.)])
    frame.attrs.update(source='synthetic_dated_open', source_date=DAY)
    monkeypatch.setattr(bot, 'fetch_cffex_quotes', lambda *args: frame)
    with pytest.raises(RuntimeError, match='independent failure after successful opening projection'):
        bot.build_live_trade_signal(product, CLOCK, 'close')
    assert len(observed) == 1
    assert observed[0] != before_anchor
    assert bot.LIVE_CONTINUATION_ANCHOR[product] is a
    assert a == before_anchor
    assert snapshot(store.root) == before_disk


@pytest.mark.parametrize('security', [None, '10011470', '10012099'])
@pytest.mark.parametrize('sleeve', ['core', 'momentum'])
def test_real_chain_split_sleeve_identity_reaches_fail_closed_policy(monkeypatch, security, sleeve):
    chain, stamp = real.wire(monkeypatch)
    a = ledger._record()['products']['IC']
    a.update(last_verified_day=PREVIOUS, post_put_contract=real.M,
             post_put_security_id='10012099', verified_momentum_put_qty=3,
             post_put_qty=17, verified_put_qty_normalized=17)
    if sleeve == 'core':
        a['v14_core_put_security_id'] = security
    else:
        a['post_put_security_id'] = security
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', a)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda *args: (chain, stamp))
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', lambda *args, **kw: {'tradable': False})
    signal = dict(market_date=DAY, next_trade_date=DAY+timedelta(days=1), close_confirmed=True,
                  option_monthly_reset_due=False, core_put_target_delta=.25, momentum_put_target_delta=.1,
                  put_target_momentum_qty=3, momentum_120=-.1, core_units_target=.5,
                  total_units_target=.5, valuation_tier=0, call_current_contract=None,
                  etf_price=7.8, future_last=7800.)
    before = deepcopy(a)
    out = bot._apply_v14_live_policy('IC', signal, )
    if sleeve == 'core' and security != '10012099':
        assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
        assert '证券' in out['v14_candidate_data_status']
    elif sleeve == 'core':
        assert out.get('iv_monitor_evidence', {}).get('security_id') == '10012099'
    assert a == before
