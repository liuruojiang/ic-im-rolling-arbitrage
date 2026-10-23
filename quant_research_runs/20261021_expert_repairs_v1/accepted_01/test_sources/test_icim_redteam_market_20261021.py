"""Independent fail-closed market audit; saved real Sina packets plus mutations.

No network, no production state, and no production code changes.
"""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import io
import re
import zipfile

import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_put_execution_v1 as execution
import test_ic_im_put_execution_repairs_20261016 as fixture

FIXTURES = Path(__file__).resolve().parent / 'tests/fixtures/icim_external_review_20261021'
REAL = (FIXTURES / 'sina_quotes_literal.bin').read_bytes().decode('gbk')
PACKETS = re.findall(r'var hq_str_CON_OP_(\d+)="([^"]*)";', REAL)


def packet(security, fields):
    return f'var hq_str_CON_OP_{security}="{",".join(fields)}";'


def fetch(monkeypatch, packets, ids=None):
    ids = ids or [row[0] for row in PACKETS]
    listing = 'var hq_str_OP_DOWN_5105002611="' + ','.join('CON_OP_' + x for x in ids) + '";'
    def response(body):
        return SimpleNamespace(content=body.encode('gbk'), headers={}, raise_for_status=lambda: None)
    get = Mock(side_effect=[response(listing), response(packets)])
    monkeypatch.setattr(bot.requests, 'get', get)
    return bot.fetch_sina_510500_chain('2611')


def test_real_three_packet_baseline_uses_trade_field(monkeypatch):
    frame, stamp = fetch(monkeypatch, REAL)
    assert len(frame) == 3
    assert frame.iloc[0]['last'] == .1669
    assert frame.iloc[0]['security_id'] == '10012482'
    assert stamp['date'] == '20261021'


@pytest.mark.parametrize('mutation', ['unrequested_security', 'wrong_underlying', 'wrong_expiry_day',
                                     'duplicate_security', 'duplicate_contract', 'missing_security'])
def test_listing_and_contract_identity_fail_closed(monkeypatch, mutation):
    rows = [(security, payload.split(',')) for security, payload in PACKETS]
    if mutation == 'unrequested_security':
        rows[0] = ('99999999', rows[0][1])
    elif mutation == 'wrong_underlying':
        rows[0][1][36] = '510300'
    elif mutation == 'wrong_expiry_day':
        rows[0][1][46] = '2026-11-01'
    elif mutation == 'duplicate_security':
        rows.append((rows[0][0], rows[0][1].copy()))
        rows[-1][1][2] = '1.5000'
    elif mutation == 'duplicate_contract':
        rows[1][1][7] = rows[0][1][7]
    elif mutation == 'missing_security':
        rows.pop(0)
    with pytest.raises(RuntimeError):
        fetch(monkeypatch, ''.join(packet(*row) for row in rows))


@pytest.mark.parametrize('field,value', [(2,'NaN'),(2,'inf'),(7,'NaN'),(7,'-1')])
def test_nonfinite_and_negative_market_fields_are_rejected(monkeypatch, field, value):
    rows = [(security, payload.split(',')) for security, payload in PACKETS]
    rows[0][1][field] = value
    with pytest.raises(RuntimeError):
        fetch(monkeypatch, ''.join(packet(*row) for row in rows))


def test_missing_security_changes_nearest_strike_selection(monkeypatch):
    baseline, _ = fetch(monkeypatch, REAL)
    rows = [(security, payload.split(',')) for security, payload in PACKETS[1:]]
    with pytest.raises(RuntimeError):
        incomplete, _ = fetch(monkeypatch, ''.join(packet(*row) for row in rows))
        target = 6.76
        original = baseline.iloc[(baseline.strike-target).abs().argmin()]
        changed = incomplete.iloc[(incomplete.strike-target).abs().argmin()]
        assert (original.contract, changed.contract) == ('510500P2611M06750', '510500P2611M07000')


def test_duplicate_conflicting_price_is_consumed_by_quote_row(monkeypatch):
    rows = [(security, payload.split(',')) for security, payload in PACKETS]
    conflicting = (rows[0][0], rows[0][1].copy())
    conflicting[1][2] = '1.5000'
    monkeypatch.setattr(bot, '_now_beijing', lambda: datetime(2026,10,21,13,25,tzinfo=bot.BEIJING))
    with pytest.raises(RuntimeError):
        chain, _ = fetch(monkeypatch, packet(*conflicting) + ''.join(packet(*row) for row in rows))
        selected = bot._quote_row(chain, '510500P2611M06750', date(2026,10,21))
        assert selected['last'] == 1.5


@pytest.mark.parametrize('consumer', ['reset','short'])
@pytest.mark.parametrize('mutation', ['missing_security','wrong_underlying','wrong_expiry_day','conflict'])
def test_real_selection_consumers_reject_corrupted_bound_chain(monkeypatch, consumer, mutation):
    rows = [(security,payload.split(',')) for security,payload in PACKETS]
    if mutation == 'missing_security':
        rows.pop(0)
    elif mutation == 'wrong_underlying':
        rows[0][1][36] = '510300'
    elif mutation == 'wrong_expiry_day':
        rows[0][1][46] = '2026-11-01'
    elif mutation == 'conflict':
        alternate = (rows[0][0],rows[0][1].copy())
        alternate[1][2] = '.2000'
        rows.insert(0,alternate)
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,10,21,13,25,tzinfo=bot.BEIJING))
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *args:.02)
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202611'])
    # Resolve a code through the correct independent ID mapping. The market
    # price still must be bound to that identity, even if this lookup succeeds.
    mapping = {'510500P2611M06750':'10012482','510500P2611M07000':'10012483',
               '510500P2611M07250':'10012484'}
    monkeypatch.setattr(bot,'fetch_sina_510500_security_id',lambda contract:mapping[contract])
    with pytest.raises(RuntimeError):
        chain,stamp = fetch(monkeypatch,''.join(packet(*row) for row in rows))
        monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda month:(chain,stamp))
        if consumer == 'reset':
            result = bot.select_ic_put_for_reset(date(2026,10,21),6.76/.95,8000,.25)
        else:
            result = bot._v14_ic_short_put_candidate(dict(market_date=date(2026,10,21),
                next_trade_date=date(2026,10,22),etf_price=6.76/.95,future_last=8000))
        assert result['contract'] == ('510500P2611M07000' if mutation == 'missing_security' else '510500P2611M06750')
        if consumer == 'short':
            assert result['tradable'] is True
            if mutation == 'conflict':
                assert result['premium'] == .2
            elif mutation == 'wrong_expiry_day':
                assert result['expiry'] == date(2026,11,25)


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zipped:
        for name, body in entries:
            zipped.writestr(name, body)
    return out.getvalue()


CSV = ('合约代码,今开盘,最高价,最低价,成交量,持仓量,今收盘,今结算\n'
       'IC2610,8000,8100,7900,12,123,8050,8040\n'
       'IC2611,8000,8100,7900,12,123,8050,8040\n'
       'IC2612,8000,8100,7900,12,123,8050,8040\n'
       'IC2703,8000,8100,7900,12,123,8050,8040\n').encode('gbk')
ARCHIVE_DAY = date(2026,9,30)
ARCHIVE_CLOCK = datetime(2026,9,30,16,tzinfo=bot.BEIJING)


def cache(monkeypatch, raw):
    monkeypatch.setattr(bot, '_CFFEX_MONTH_CACHE', {'202609': raw})
    monkeypatch.setattr(bot, '_latest_completed_exchange_day', lambda *args: ARCHIVE_DAY)


@pytest.mark.parametrize('bad', ['missing_columns', 'empty_csv', 'bad_encoding', 'invalid_numeric'])
def test_failed_archive_parse_or_validation_does_not_poison_cache(monkeypatch, bad):
    body = {'missing_columns': b'instrument,last\nIC2610,8050\n', 'empty_csv': b'',
            'bad_encoding': b'\xff', 'invalid_numeric': CSV.replace(b',8050,8040',b',NaN,8040')}[bad]
    raw = archive([('20260930_1.csv',body)])
    cache(monkeypatch,raw)
    with pytest.raises(Exception):
        bot._cffex_historical_quote_frame('IC', ARCHIVE_DAY, ARCHIVE_CLOCK)
    assert '202609' not in bot._CFFEX_MONTH_CACHE


def test_bad_schema_cache_does_not_block_later_valid_recovery(monkeypatch):
    raw = archive([('20260930_1.csv',b'instrument,last\nIC2610,8050\n')])
    cache(monkeypatch,raw)
    good = archive([('20260930_1.csv',CSV)])
    response = SimpleNamespace(headers={},raise_for_status=lambda:None,
                               iter_content=lambda **kwargs:iter([good]),close=lambda:None)
    get = Mock(return_value=response)
    monkeypatch.setattr(bot.requests,'get',get)
    with pytest.raises(RuntimeError):
        bot._cffex_historical_quote_frame('IC',ARCHIVE_DAY,ARCHIVE_CLOCK)
    # No fixture mutation or manual cache clearing: the next caller must be
    # able to re-download the corrected source instead of replaying bad bytes.
    recovered = bot._cffex_historical_quote_frame('IC',ARCHIVE_DAY,ARCHIVE_CLOCK)
    assert len(recovered) == 4
    assert get.call_count == 1


@pytest.mark.parametrize('bad', ['missing_day_twice','duplicate','encrypted','invalid_zip'])
def test_archive_shape_failures_evict_cache(monkeypatch, bad):
    raw = archive([('20260930_1.csv',CSV)])
    if bad == 'missing_day_twice':
        raw = archive([('20260929_1.csv',CSV)])
    elif bad == 'duplicate':
        with pytest.warns(UserWarning):
            raw = archive([('20260930_1.csv',CSV),('20260930_1.csv',CSV)])
    elif bad == 'encrypted':
        altered = bytearray(raw)
        position = altered.index(b'PK\x01\x02')
        altered[position+8] |= 1
        raw = bytes(altered)
    elif bad == 'invalid_zip':
        raw = b'PKbroken'
    cache(monkeypatch,raw)
    response = SimpleNamespace(headers={}, raise_for_status=lambda: None,
                               iter_content=lambda **kwargs: iter([raw]), close=lambda: None)
    get = Mock(return_value=response)
    monkeypatch.setattr(bot.requests,'get',get)
    with pytest.raises(RuntimeError):
        bot._cffex_historical_quote_frame('IC', ARCHIVE_DAY, ARCHIVE_CLOCK)
    assert '202609' not in bot._CFFEX_MONTH_CACHE
    assert get.call_count == (1 if bad == 'missing_day_twice' else 0)


def test_missing_day_refresh_transport_failure_does_not_keep_old_cache(monkeypatch):
    cache(monkeypatch,archive([('20260929_1.csv',CSV)]))
    get = Mock(side_effect=bot.requests.Timeout('synthetic transport outage'))
    monkeypatch.setattr(bot.requests,'get',get)
    with pytest.raises(bot.requests.Timeout):
        bot._cffex_historical_quote_frame('IC',ARCHIVE_DAY,ARCHIVE_CLOCK)
    assert '202609' not in bot._CFFEX_MONTH_CACHE
    assert get.call_count == 1


def test_standard_m_gate_and_non_ic_compatibility():
    execution.require_supported_ic_contracts('IC', fixture.anchor('IC'))
    execution.require_supported_ic_contracts('IM', {'post_put_contract':'510500P2612A07500'})


@pytest.mark.parametrize('field', ['v14_core_put_contract','post_put_contract','put_current_contract',
                                  'put_target_contract','v14_short_put_contract',
                                  'v14_profit_old_contract','v14_profit_reentry_contract'])
def test_declared_adjusted_gap_is_safely_isolated(field):
    with pytest.raises(RuntimeError,match='adjusted contract master'):
        execution.require_supported_ic_contracts('IC',{field:'510500P2612A07500'})


def monitor_signal():
    return dict(market_date=date(2026,10,21),close_confirmed=False,etf_price=7.8,
                etf_quote_date='20261021',etf_quote_time='132900',
                put_current_contract='510500P2611M06500',iv_monitor_option_price=.1,
                sse_time='090000',iv_monitor_evidence=dict(
                    contract='510500P2612M07500',security_id='10012099',option_price=.8,
                    strike=7.5,expiry=date(2026,12,23),source='independent-test',
                    contract_unit=10000,quote_date='20261021',quote_time='132900'))


def test_iv_valid_bound_core_ignores_other_leg_price_and_stale_time(monkeypatch):
    signal = monitor_signal()
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *args:.02)
    warning = bot.build_iv_warning('IC',signal,{},None,
                                  datetime(2026,10,21,13,30,tzinfo=bot.BEIJING))
    assert warning['contract'] == '510500P2612M07500'
    assert warning['iv'] is not None


@pytest.mark.parametrize('field,value', [
    ('option_price',float('nan')),('option_price',float('inf')),('strike',float('nan')),
    ('quote_time','093000'),('quote_time','133001'),('quote_date','20261020'),
    ('strike',6.5),('expiry',date(2026,11,25)),('contract_unit',10180),
    ('security_id',None),('source',None)])
def test_iv_malformed_or_stale_bound_evidence_cannot_use_other_leg(monkeypatch,field,value):
    signal = monitor_signal()
    signal['iv_monitor_evidence'][field] = value
    pricing = Mock(side_effect=AssertionError('must not consume another leg'))
    monkeypatch.setattr(bot,'_implied_volatility',pricing)
    warning = bot.build_iv_warning('IC',signal,{},None,
                                  datetime(2026,10,21,13,30,tzinfo=bot.BEIJING))
    assert warning['iv'] is None
    pricing.assert_not_called()


def test_core_overlay_rejects_security_identity_mismatch(monkeypatch):
    anchor = fixture.anchor('IC')
    signal = fixture.signal('IC',date(2026,10,21),close_confirmed=False)
    clock = datetime(2026,10,21,13,30,tzinfo=bot.BEIJING)
    frame = pd.DataFrame([dict(contract=anchor['v14_core_put_contract'],last=.8,strike=7.5,
        security_id='99999999',source_stamp=clock.replace(minute=29))])
    monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda month:
        (frame,dict(date='20261021',time='132900',source='independent-test')))
    monkeypatch.setattr(bot,'_now_beijing',lambda:clock)
    with pytest.raises(RuntimeError):
        bot._v14_ic_core_overlay(signal,anchor)


def test_stale_row_remains_visible_but_cannot_be_consumed(monkeypatch):
    rows = [(security,payload.split(',')) for security,payload in PACKETS]
    rows[0][1][32] = '2026-10-21 09:30:00'
    chain,_ = fetch(monkeypatch,''.join(packet(*row) for row in rows))
    assert len(chain) == 3
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,10,21,13,30,tzinfo=bot.BEIJING))
    assert bot._quote_row(chain,'510500P2611M06750',date(2026,10,21)) is None


def test_full_real_adjusted_chain_preserves_fifteen_legal_m_contracts(monkeypatch):
    responses = [SimpleNamespace(content=(FIXTURES/name).read_bytes(),headers={},
        raise_for_status=lambda:None) for name in ('sina_december_listing.bin','sina_december_quotes.bin')]
    monkeypatch.setattr(bot.requests,'get',Mock(side_effect=responses))
    chain,_ = bot.fetch_sina_510500_chain('2612')
    assert len(chain) == 15
    assert chain.contract.str.fullmatch(r'510500P2612M\d{5}').all()
    assert len(chain.attrs['unresolved_adjusted_contracts']) == 14


@pytest.mark.parametrize('consumer', ['reset','short'])
def test_second_security_lookup_cannot_replace_validated_chain_identity(monkeypatch,consumer):
    # The first retrieval is authentic and passes all repaired chain gates.
    chain,stamp = fetch(monkeypatch,REAL)
    assert chain.iloc[0]['security_id'] == '10012482'
    rows = [(security,payload.split(',')) for security,payload in PACKETS]
    rows[0] = ('99999999',rows[0][1])
    listing = 'var hq_str_OP_DOWN_5105002611="' + ','.join('CON_OP_'+x[0] for x in PACKETS) + '";'
    details = ''.join(packet(*row) for row in rows)
    responses = [SimpleNamespace(content=body.encode('gbk'),headers={},raise_for_status=lambda:None)
                 for body in (listing,details)]
    monkeypatch.setattr(bot.requests,'get',Mock(side_effect=responses))
    monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda month:(chain,stamp))
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202611'])
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *args:.02)
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,10,21,13,25,tzinfo=bot.BEIJING))
    # Correct reuse of the validated row or earlier rejection both satisfy the
    # identity contract; an unrelated ID can never replace it.
    try:
        if consumer == 'reset':
            result = bot.select_ic_put_for_reset(date(2026,10,21),6.76/.95,8000,.25)
        else:
            result = bot._v14_ic_short_put_candidate(dict(market_date=date(2026,10,21),
                next_trade_date=date(2026,10,22),etf_price=6.76/.95,future_last=8000))
    except RuntimeError:
        return
    assert result['security_id'] == '10012482'


@pytest.mark.parametrize('mutation',['control','unrequested_security','wrong_underlying','wrong_expiry_day','fractional_strike'])
def test_sse_fallback_security_lookup_uses_same_strict_parser(monkeypatch,mutation):
    rows = [(security,payload.split(',')) for security,payload in PACKETS]
    if mutation == 'unrequested_security':
        rows[0] = ('99999999',rows[0][1])
    elif mutation == 'wrong_underlying':
        rows[0][1][36] = '510300'
    elif mutation == 'wrong_expiry_day':
        rows[0][1][46] = '2026-11-01'
    elif mutation == 'fractional_strike':
        rows[0][1][7] = '6.7504'
    listing = 'var hq_str_OP_DOWN_5105002611="' + ','.join('CON_OP_'+x[0] for x in PACKETS) + '";'
    details = ''.join(packet(*row) for row in rows)
    responses = [SimpleNamespace(content=body.encode('gbk'),headers={},raise_for_status=lambda:None)
                 for body in (listing,details)]
    monkeypatch.setattr(bot.requests,'get',Mock(side_effect=responses))
    if mutation == 'control':
        assert bot.fetch_sina_510500_security_id('510500P2611M06750') == '10012482'
    else:
        with pytest.raises(RuntimeError):
            bot.fetch_sina_510500_security_id('510500P2611M06750')
