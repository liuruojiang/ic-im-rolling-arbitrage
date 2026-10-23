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
    # Windows' msvcrt coordination lock is not ledger payload: append opens
    # it before validation and may append a NUL even on a rejected write.
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != '.ledger.lock'}


def recent_store(root):
    record = ledger._record()
    record.update(verified_day=PREVIOUS.isoformat(), signals={})
    for a in record['products'].values():
        a.update(last_verified_day=PREVIOUS.isoformat(), verified_grid_entry_source='none',
                 verified_next_grid_entry_source='none')
    record['digest'] = state._digest(record)
    state._validate_record(record)
    store = state.StateStore(root)
    store._atomic_write(store.journal_dir / f'000000-{PREVIOUS}.json', record)
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
        # apply_policy describes close targets; persist the synchronized
        # nonfuture holdings just as the StateStore does at close.
        if product == 'IM':
            a.update(verified_core_put_qty_normalized=0., verified_parent_puts=0,
                     verified_total_put_qty_normalized=0., verified_put_qty_normalized=0.,
                     post_core_put_equivalent_units=0., post_put_equivalent_units=0.)
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
                          dict(instrument='IC2612', openprice=7800.),
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


def test_old_m_replay_must_not_synthesize_standard_identity_from_directed_id(monkeypatch):
    # The official raw fixture proves this old security is now A07500, with
    # adjusted strike 7.367 and unit 10180, although the anchor still says M.
    official = real.payload()
    adjusted = next(r for r in official['list'] if r[5] == '10011470')
    assert adjusted[0] == real.A and adjusted[4] == 7.367
    monkeypatch.setattr(bot, 'fetch_option_closes', lambda security:
                        pd.Series([.45], index=pd.to_datetime([DAY])))
    a = ledger._record()['products']['IC']
    a['v14_core_put_security_id'] = '10011470'
    signal = dict(market_date=DAY, core_put_target_delta=.25,
                  option_monthly_reset_due=False, put_target_momentum_qty=0,
                  etf_price=7.8, future_last=7800.)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    token = bot._HISTORICAL_REPLAY_DAY.set(DAY)
    try:
        with pytest.raises(RuntimeError):
            bot._v14_ic_core_overlay(signal, a)
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    assert 'iv_monitor_evidence' not in signal


def builder_inputs(monkeypatch, anchor, chain, stamp):
    """Inject observations, leaving actual builder/date/quote/identity gates."""
    template = json.loads(ledger.FIXTURE.read_text(encoding='utf-8'))['signals']['IC']
    live = dict(template, history_date=DAY, price=7800., score=3.,
                momentum_current_weight=0., momentum_next_weight=0.,
                momentum_signal_date=DAY, momentum_120=-.1,
                grid_current_units=0., grid_target_units=0.,
                valuation_provenance={'audit': 'synthetic'},
                v13_put_target_delta=float(anchor['verified_total_put_delta']),
                v13_core_put_delta=float(anchor['verified_core_put_delta']),
                v13_momentum_put_delta=float(anchor['verified_momentum_put_delta']))
    monkeypatch.setattr(bot, 'live_proxy', lambda *args: deepcopy(live))
    monkeypatch.setattr(bot.fear_grid, 'fetch_score', lambda *args: dict(
        fear_greed_index=40., fear_data_status='same_day_post_close', fear_data_date=DAY,
        fear_page_updated_at=DAY.isoformat()+'T15:01:00+08:00', fear_csv_sha256='a'*64,
        fear_retrieved_at=DAY.isoformat()+'T15:30:00+08:00', fear_source_url=bot.fear_grid.CSV_URL))
    monkeypatch.setattr(bot.fear_grid, 'fetch_jiu_reference', lambda *args: {})
    futures = pd.DataFrame([dict(instrument='IC'+month, lastprice=7800.,
        bprice=7799., sprice=7801., volume=1000., position=2000.)
        for month in ['2611', '2612', '2703', '2706']])
    futures.attrs.update(source='synthetic_full_future_chain', source_date=DAY,
                         listed_instruments=list(futures.instrument))
    monkeypatch.setattr(bot, 'fetch_cffex_quotes', lambda *args: futures)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda *args: (chain, stamp))
    monkeypatch.setattr(bot, 'fetch_sse_510500_quote', lambda: dict(
        date='20261021',time='150000',last=7.8))
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *args: .02)
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', lambda *args, **kw: {'tradable': False})


@pytest.mark.parametrize('security', ['10012099', '10011470', None])
def test_full_public_builder_real_sse_current_holding_identity(tmp_path, monkeypatch, security):
    store, record = recent_store(tmp_path / 'ledger')
    anchors = state.anchors_from_record(record)
    a = anchors['IC']
    a.update(post_put_contract=real.M,post_put_security_id=security,
             v14_core_put_security_id=security)
    chain, stamp = real.wire(monkeypatch)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', anchors)
    builder_inputs(monkeypatch, a, chain, stamp)
    before, disk = deepcopy(a), snapshot(store.root)
    if security == '10012099':
        out = bot.build_live_trade_signal('IC', CLOCK, 'close')
        assert out['v14_core_put_current_contract'] == real.M
        assert out['iv_monitor_evidence']['security_id'] == security
        assert out['v14_action'] != 'HOLD_FAIL_CLOSED'
    else:
        with pytest.raises(RuntimeError, match='证券'):
            bot.build_live_trade_signal('IC', CLOCK, 'close')
    assert a == before
    assert bot.LIVE_CONTINUATION_ANCHOR['IC'] is a
    assert snapshot(store.root) == disk


@pytest.mark.parametrize('bad_sleeve', [None, 'core', 'momentum'])
def test_actual_builder_independent_sleeves_never_cross_identity(tmp_path, monkeypatch, bad_sleeve):
    store, record = recent_store(tmp_path / 'ledger')
    anchors = state.anchors_from_record(record)
    a = anchors['IC']
    chain, stamp = real.wire(monkeypatch)
    other = chain[chain.contract.str.match('510500P2612M') & ~chain.contract.eq(real.M)].iloc[0]
    a.update(post_put_contract=str(other.contract),post_put_security_id=str(other.security_id),
             verified_momentum_put_qty=3,verified_momentum_put_delta=.125,
             verified_total_put_delta=.375,post_put_qty=17,verified_put_qty_normalized=17)
    if bad_sleeve == 'core':
        a['v14_core_put_security_id'] = '10011470'
    elif bad_sleeve == 'momentum':
        a['post_put_security_id'] = '10011470'
    policy.validate_extension('IC', a)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', anchors)
    builder_inputs(monkeypatch, a, chain, stamp)
    before = deepcopy(a)
    if bad_sleeve == 'momentum':
        with pytest.raises(RuntimeError, match='证券'):
            bot.build_live_trade_signal('IC', CLOCK, 'close')
    else:
        out = bot.build_live_trade_signal('IC', CLOCK, 'close')
        if bad_sleeve == 'core':
            assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
            assert not out.get('v14_ordinary_put_pending')
        else:
            assert out['iv_monitor_evidence']['contract'] == real.M
            assert out['iv_monitor_evidence']['security_id'] == '10012099'
            assert out['put_current_contract'] == str(other.contract)
            assert out['put_current_momentum_qty'] == 3
    assert a == before


def test_legal_zero_put_holding_does_not_require_unheld_legacy_identity(tmp_path, monkeypatch):
    store, record = recent_store(tmp_path / 'ledger')
    anchors = state.anchors_from_record(record)
    a = anchors['IC']
    ledger._empty_core(a, 'IC')
    a.update(post_put_contract=real.M,post_put_security_id='10011470',
             verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    record['products']['IC'] = state._jsonable(a)
    record['digest'] = state._digest(record)
    state._validate_record(record)
    chain, stamp = real.wire(monkeypatch)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', anchors)
    builder_inputs(monkeypatch, a, chain, stamp)
    before = deepcopy(a)
    out = bot.build_live_trade_signal('IC', CLOCK, 'close')
    assert out['put_current_core_qty'] == out['put_current_momentum_qty'] == 0
    assert out['v14_core_put_contract'] is None
    assert not out.get('v14_ordinary_put_pending')
    assert a == before


class SyntheticCloseClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return CLOCK.astimezone(tz) if tz else CLOCK.replace(tzinfo=None)


def close_evidence(signal):
    signal.update(score=3.,grid_action='HOLD',grid_entry_source_current='none',
        grid_entry_source_target='none',grid_transition_reason='hold_flat',
        fear_greed_index=40.,fear_data_date=DAY,fear_data_status='same_day_post_close',
        fear_source_url=bot.fear_grid.CSV_URL,fear_csv_sha256='a'*64,
        fear_page_updated_at=DAY.isoformat()+'T15:01:00+08:00',
        fear_retrieved_at=DAY.isoformat()+'T15:30:00+08:00',
        signal_generated_at=CLOCK.isoformat(),signal_generation_timing='close_confirmed')
    return signal


@pytest.mark.parametrize('product', ['IC','IM'])
def test_current_boundary_ordinary_open_real_hash_ledger_roundtrip(tmp_path,monkeypatch,product):
    # A consistent synthetic post-close wall clock exercises all unchanged
    # source/generation/write-time checks; no effective date is moved.
    for module in [state,bot,bot.fear_grid]:
        monkeypatch.setattr(module,'datetime',SyntheticCloseClock)
    store,record=recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    prior=ledger._signal(product,anchors[product],PREVIOUS,momentum=True)
    record['products'][product]['v14_ordinary_put_pending']=state._jsonable(prior['v14_ordinary_put_pending'])
    record['digest']=state._digest(record)
    state._validate_record(record)
    store._atomic_write(store.journal_dir/'000000-2026-10-20.json',record)
    store._atomic_write(store.latest_path,record)
    record=store.load_latest()
    anchors=state.anchors_from_record(record)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors)
    monkeypatch.setattr(bot,'fetch_option_dated_open',lambda *args:(.35,'synthetic_dated_open'))
    plan=anchors[product]['v14_ordinary_put_pending']
    contracts={leg[f'{side}_contract'] for leg in plan['legs'].values()
               for side in ['old','new'] if float(leg[f'{side}_qty'])>0}
    frame=pd.DataFrame([dict(instrument=c,openprice=350.) for c in contracts])
    frame.attrs.update(source='synthetic_dated_open',source_date=DAY)
    monkeypatch.setattr(bot,'fetch_cffex_quotes',lambda *args:frame)
    opening=bot._v14_ordinary_open_evidence(product,DAY,CLOCK,{})
    assert opening['status']=='confirmed_open_research_price'
    other='IM' if product=='IC' else 'IC'
    signals={product:close_evidence(ledger._signal(product,anchors[product],DAY,
                                                 momentum=True,opening=opening)),
             other:close_evidence(ledger._signal(other,anchors[other],DAY))}
    for s in signals.values():
        s['v14_opening_events']=[]
    result=store.append_confirmed_signals(record,signals)
    reopened=state.StateStore(store.root).load_latest()
    assert result['digest']==reopened['digest']
    assert reopened['sequence']==record['sequence']+1
    field='verified_momentum_put_qty' if product=='IC' else 'verified_momentum_put_qty_normalized'
    assert reopened['products'][product][field]==(3 if product=='IC' else 1.5)
    assert reopened['products'][product]['v14_ordinary_put_pending'] is None
    before=snapshot(store.root)
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(reopened,signals)
    assert snapshot(store.root)==before


def test_actual_short_mark_must_not_trust_stale_m_label_for_adjusted_security(tmp_path,monkeypatch):
    store,record=recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    a=anchors['IC']
    ledger._empty_core(a,'IC')
    a.update(v14_route_state='short_put',v14_cycle_id='synthetic-stale-m-held-identity',
        v14_short_put_contract=real.M,v14_short_put_security_id='10011470',
        v14_short_put_qty_normalized=5.,v14_short_put_entry_premium=.4,
        v14_short_put_expiry=date(2026,12,23),v14_short_put_cost_basis='confirmed_dated_open',
        v14_short_put_entry_price_day=PREVIOUS,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    # This is a synthetic accepted legacy anchor, not a claim of an actual
    # July trade. Official raw data, unlike its stale M label, identifies A.
    record['products']['IC']=state._jsonable(a)
    record['digest']=state._digest(record)
    state._validate_record(record)
    chain,stamp=real.wire(monkeypatch)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors)
    builder_inputs(monkeypatch,a,chain,stamp)
    live=bot.live_proxy('IC',CLOCK)
    live.update(momentum_120=.1,valuation_tier=1)
    monkeypatch.setattr(bot,'live_proxy',lambda *args:deepcopy(live))
    candidate=dict(tradable=True,contract='510500P2701M06500',security_id='10015000',
        expiry=date(2027,1,27),premium=.3,iv=.8,qty_normalized=5.)
    monkeypatch.setattr(bot,'_v14_ic_short_put_candidate',lambda *args,**kw:candidate)
    monkeypatch.setattr(bot,'fetch_option_closes',lambda security:
        pd.Series([.01],index=pd.to_datetime([DAY])))
    before=deepcopy(a)
    disk=snapshot(store.root)
    # The producer rejects its marked adapter fallback before returning any
    # new signal. Adapter HOLD assertions remain independently tested below.
    with pytest.raises(RuntimeError,match='unresolved held Put identity'):
        bot.build_live_trade_signal('IC',CLOCK,'close')
    assert a==before
    assert snapshot(store.root)==disk


@pytest.mark.parametrize('outcome',[None,'assigned','expired_worthless'])
@pytest.mark.parametrize('candidate_outage',[False,True])
def test_short_identity_failure_cannot_publish_or_complete_expiry_branch(monkeypatch,outcome,candidate_outage):
    expiry=date(2026,12,23)
    a=ledger._record()['products']['IC']
    ledger._empty_core(a,'IC')
    a.update(last_verified_day=date(2026,12,22),v14_route_state='short_put',
        v14_cycle_id='synthetic-stale-m-expiry',v14_short_put_contract=real.M,
        v14_short_put_security_id='10011470',v14_short_put_qty_normalized=5.,
        v14_short_put_entry_premium=.4,v14_short_put_expiry=expiry,
        v14_short_put_cost_basis='confirmed_dated_open',v14_short_put_entry_price_day=PREVIOUS,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    policy.validate_extension('IC',a)
    chain,stamp=real.wire(monkeypatch)
    # Shift the observed quote day for this synthetic expiry stress. The code,
    # security ID and adjusted strike remain the real saved SSE identities.
    stamp=dict(stamp,date=expiry.isoformat(),time='150000')
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,'IC',a)
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,12,23,15,40,tzinfo=bot.BEIJING))
    monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda *args:(chain,stamp))
    def candidate(*args,**kwargs):
        if candidate_outage:
            raise RuntimeError('synthetic candidate outage preceding old late identity check')
        return {'tradable':False}
    monkeypatch.setattr(bot,'_v14_ic_short_put_candidate',candidate)
    monkeypatch.setattr(bot,'fetch_option_closes',lambda security:
        pd.Series([.01],index=pd.to_datetime([expiry])))
    signal=seed.fixture.signal('IC',expiry,next_trade_date=date(2026,12,24),
        core_put_target_delta=0.,put_target_core_qty=0,put_target_momentum_qty=0,
        momentum_120=.1,v14_recovery_net_pnl_mark=-.1)
    if outcome is not None:
        signal.update(v14_model_settlement_outcome=outcome,v14_model_settlement_confirmed=True)
    before=deepcopy(a)
    out=bot._apply_v14_live_policy('IC',signal)
    assert out['v14_action']=='HOLD_FAIL_CLOSED'
    assert out['v14_route_state']=='short_put'
    assert not out['v14_settlement_pending']
    assert a==before


@pytest.mark.parametrize('candidate_outage',[False,True])
def test_valid_short_identity_expiry_survives_independent_candidate_outage(monkeypatch,candidate_outage):
    expiry=date(2026,12,23)
    a=ledger._record()['products']['IC']
    ledger._empty_core(a,'IC')
    a.update(last_verified_day=date(2026,12,22),v14_route_state='short_put',
        v14_cycle_id='synthetic-standard-m-expiry',v14_short_put_contract=real.M,
        v14_short_put_security_id='10012099',v14_short_put_qty_normalized=5.,
        v14_short_put_entry_premium=.4,v14_short_put_expiry=expiry,
        v14_short_put_cost_basis='confirmed_dated_open',v14_short_put_entry_price_day=PREVIOUS,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    policy.validate_extension('IC',a)
    chain,stamp=real.wire(monkeypatch)
    stamp=dict(stamp,date=expiry.isoformat(),time='150000')
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,'IC',a)
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,12,23,15,40,tzinfo=bot.BEIJING))
    monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda *args:(chain,stamp))
    def candidate(*args,**kwargs):
        if candidate_outage:
            raise RuntimeError('synthetic unrelated candidate outage')
        return {'tradable':False}
    monkeypatch.setattr(bot,'_v14_ic_short_put_candidate',candidate)
    monkeypatch.setattr(bot,'fetch_option_closes',lambda security:
        pd.Series([.01],index=pd.to_datetime([expiry])))
    signal=seed.fixture.signal('IC',expiry,next_trade_date=date(2026,12,24),
        core_put_target_delta=0.,put_target_core_qty=0,put_target_momentum_qty=0,momentum_120=.1)
    before=deepcopy(a)
    out=bot._apply_v14_live_policy('IC',signal)
    assert out['v14_action']=='PUBLISH_SHORT_PUT_EXPIRY_BRANCHES'
    assert out['v14_settlement_pending']
    assert out['v14_route_state']=='short_put'
    assert a==before


def test_full_builder_december_expiry_stays_closed_without_2027_calendar(tmp_path,monkeypatch):
    expiry=date(2026,12,23)
    monkeypatch.setitem(globals(),'DAY',expiry)
    monkeypatch.setitem(globals(),'PREVIOUS',date(2026,12,22))
    monkeypatch.setitem(globals(),'CLOCK',datetime(2026,12,23,15,40,tzinfo=bot.BEIJING))
    store,record=recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    a=anchors['IC']
    ledger._empty_core(a,'IC')
    a.update(post_core_contract='IC2703',v14_route_state='short_put',
        v14_cycle_id='synthetic-expiry-builder',v14_short_put_contract=real.M,
        v14_short_put_security_id='10011470',v14_short_put_qty_normalized=5.,
        v14_short_put_entry_premium=.4,v14_short_put_expiry=expiry,
        v14_short_put_cost_basis='confirmed_dated_open',v14_short_put_entry_price_day=PREVIOUS,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    record['products']['IC']=state._jsonable(a)
    record['digest']=state._digest(record)
    state._validate_record(record)
    body=real.payload()
    body['date']=20261223
    chain,stamp=real.wire(monkeypatch,body)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors)
    builder_inputs(monkeypatch,a,chain,stamp)
    monkeypatch.setattr(bot,'fetch_sse_510500_quote',lambda:dict(date='20261223',time='150000',last=7.8))
    monkeypatch.setattr(bot,'fetch_option_closes',lambda security:
        pd.Series([.01],index=pd.to_datetime([expiry])))
    before,disk=deepcopy(a),snapshot(store.root)
    # Do not bypass this independent gate to manufacture full-builder
    # reachability for the adapter expiry probes above.
    with pytest.raises(RuntimeError,match='2027'):
        bot.build_live_trade_signal('IC',CLOCK,'close')
    assert bot.LIVE_CONTINUATION_ANCHOR['IC'] is a and a==before
    assert snapshot(store.root)==disk


@pytest.mark.parametrize('boundary',['execution','policy','new_signal','store'])
def test_unverified_identity_marker_rejected_before_projection_or_write(tmp_path,monkeypatch,boundary):
    store,record=recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    signals={p:ledger._signal(p,anchors[p],DAY) for p in state.PRODUCTS}
    signals['IC']['v14_held_put_identity_status']='unverified'
    before_disk,before_anchors=snapshot(store.root),deepcopy(anchors)
    with pytest.raises(RuntimeError,match='unresolved held Put identity'):
        if boundary=='execution':
            execution.require_resolved_held_identity('IC',signals['IC'])
        elif boundary=='policy':
            policy.apply_policy('IC',signals['IC'],anchors['IC'],candidate={'tradable':False})
        elif boundary=='new_signal':
            state.validate_v14_new_signal(signals['IC'],'IC')
        else:
            store.append_confirmed_signals(record,signals)
    assert snapshot(store.root)==before_disk
    assert anchors==before_anchors
    assert state.StateStore(store.root).load_latest()['digest']==record['digest']
