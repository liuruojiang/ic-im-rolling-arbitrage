"""Independent third-batch valuation reachability tests.

VIP snapshots, prices and hostile proxy scores here are synthetic; no network,
production ledger or account action is used. Saved SSE quote bytes are used only
to keep the IC builder's identity gate independent of the valuation experiment.
"""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import ic_im_daily_valuation as valuation
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round2_audit_lifecycle_20261021 as lifecycle
import test_ic_im_external_review_round2_20261021 as real
import test_ic_im_ordinary_put_route_fix_20261013 as ledger

DAY = date(2026,10,21)
PREVIOUS = date(2026,10,20)
CLOCK = datetime(2026,10,21,18,0,tzinfo=bot.BEIJING)


class SyntheticCloseClock(datetime):
    @classmethod
    def now(cls,tz=None):
        return CLOCK.astimezone(tz) if tz else CLOCK.replace(tzinfo=None)


def snapshot(day=DAY):
    return dict(schema_version=1,valuation_date=day.isoformat(),
                fetched_at=day.isoformat()+'T16:00:00+08:00',
                source_urls=valuation.SOURCE_URLS,evidence_sha256='a'*64,
                indices={code:dict(pe_aggregate_ttm=80.,pb_aggregate=5.) for code in ['000905','000852']})


def valuation_input(monkeypatch,tmp_path,value):
    monkeypatch.setattr(valuation,'DEFAULT_FILE',tmp_path/'no_valuation_snapshot.json')
    monkeypatch.delenv(valuation.FILE_ENV,raising=False)
    monkeypatch.setenv(valuation.ENV,'' if value is None else json.dumps(value))


def full_frame(product,end=DAY):
    if product=='IC':
        import ic_mainline_v1_3 as local
        path=local.CSI500_OHLCV_PATH
    else:
        import im_mainline_v1_3 as local
        path=local.CSI1000_OHLCV_PATH
    fixed=pd.read_csv(path,parse_dates=['date'])['date']
    extra=[p for p in pd.date_range(bot.DATA_CUTOFF+pd.Timedelta(days=1),end)
           if bot._is_exchange_trading_day(p.date())]
    index=pd.DatetimeIndex([*fixed,*extra]).drop_duplicates().sort_values()
    price=pd.Series(np.linspace(6000.,7800.,len(index)),index=index)
    return pd.DataFrame(dict(open=price,high=price*1.01,low=price*.99,close=price,volume=1e9))


def live_sources(monkeypatch,tmp_path,product,*,proxy_score=0.,vip=None):
    valuation_input(monkeypatch,tmp_path,snapshot() if vip is None else vip)
    frame=full_frame(product)
    monkeypatch.setattr(bot,'fetch_ohlcv_history',lambda _:frame.copy())
    monkeypatch.setattr(bot,'_proxy_score_for_price',lambda *args:proxy_score)
    monkeypatch.setattr(bot.chinabond,'resolve',lambda *a,**k:dict(
        yield_decimal=.02,mode='frozen',source_date=None,reason='synthetic audit rate',
        used_date=bot.DATA_CUTOFF.isoformat(),observed_date=None))
    return frame


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('proxy_score',[-100.,100.])
def test_actual_score_and_next_put_targets_survive_opposite_legacy_proxy(product,proxy_score,monkeypatch,tmp_path):
    live_sources(monkeypatch,tmp_path,product,proxy_score=proxy_score)
    value=bot.live_proxy(product,CLOCK)
    expected=bot.valuation_score(5.,1/80.-.02,bot.FROZEN[product]['dividend'])
    assert value['valuation_provenance']['mode']=='vip_actual'
    assert value['score']==expected==3.5
    if product=='IC':
        assert value['v13_core_put_delta']==.5
        assert value['valuation_put_delta']==1.
    else:
        assert value['v13_parent_puts_per_full_core']==4
        assert value['v13_put_target_qty_normalized']==2.
    # Only compatibility current values still depend on the hostile proxy;
    # they are not independently verified holdings and need the next boundary.
    if proxy_score<0:
        assert value['grid_current_units']>0
    else:
        assert value['grid_current_units']==0


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('next_session',[False,True])
def test_anchor_overwrites_proxy_current_state_without_overwriting_real_target(product,next_session,monkeypatch,tmp_path):
    live_sources(monkeypatch,tmp_path,product,proxy_score=-100.)
    value=bot.live_proxy(product,CLOCK)
    anchor=deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
    anchor.update(last_verified_day=PREVIOUS if next_session else DAY,
                  verified_grid_units=0.,verified_next_grid_units=.5,
                  verified_momentum_weight=0.,verified_next_momentum_weight=.5)
    if product=='IC':
        anchor.update(verified_core_put_delta=.125,verified_momentum_put_delta=0.,
                      verified_total_put_delta=.125,
                      verified_core_put_driver='synthetic verified core',
                      verified_momentum_put_driver='synthetic verified momentum')
    else:
        anchor.update(verified_parent_puts=1,verified_core_put_qty_normalized=.5,
                      verified_momentum_put_qty_normalized=0.,verified_total_put_qty_normalized=.5)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,product,anchor)
    out=bot._apply_next_unverified_session_anchor(product,value,next_session=next_session)
    assert out['score']==3.5 and out['valuation_provenance']['mode']=='vip_actual'
    assert out['grid_current_units']==(.5 if next_session else 0.)
    assert out['momentum_current_weight']==(.5 if next_session else 0.)
    assert out['state_anchor_day']==anchor['last_verified_day']
    if product=='IC':
        assert out['v13_current_core_put_delta']==.125
        assert out['v13_core_put_delta']==.5
    else:
        assert out['v13_current_put_qty_normalized']==.5
        assert out['v13_put_target_qty_normalized']==2.


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('kind',['missing','stale','future','partial','before_fetch','intraday'])
def test_authorized_proxy_fallback_remains_explicit_and_date_exact(product,kind,monkeypatch,tmp_path):
    value=snapshot()
    clock=CLOCK
    if kind=='missing':value=None
    if kind=='stale':value=snapshot(date(2026,10,20))
    if kind=='future':value=snapshot(date(2026,10,22))
    if kind=='partial':value['indices'].pop('000905')
    if kind=='before_fetch':clock=datetime(2026,10,21,15,30,tzinfo=bot.BEIJING)
    if kind=='intraday':clock=datetime(2026,10,21,14,30,tzinfo=bot.BEIJING)
    valuation_input(monkeypatch,tmp_path,value)
    out=valuation.resolve(product,DAY,clock,price=7800.,frozen=bot.FROZEN[product],anchor_day=bot.DATA_CUTOFF)
    assert out['provenance']['mode']=='proxy'
    assert out['pe']==pytest.approx(bot.FROZEN[product]['pe']*7800./bot.FROZEN[product]['price'])
    assert '代理估值' in valuation.disclosure(out['provenance'])


@pytest.mark.parametrize('proxy_score',[-100.,100.])
def test_complete_ic_builder_replaces_legacy_state_before_formal_target(proxy_score,monkeypatch,tmp_path):
    for module in [bot,state,bot.fear_grid]:
        monkeypatch.setattr(module,'datetime',SyntheticCloseClock)
    store,record=lifecycle.recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    chain,stamp=real.wire(monkeypatch)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors)
    original_live_proxy=bot.live_proxy
    lifecycle.builder_inputs(monkeypatch,anchors['IC'],chain,stamp)
    monkeypatch.setattr(bot,'live_proxy',original_live_proxy)
    live_sources(monkeypatch,tmp_path,'IC',proxy_score=proxy_score)
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202612'])
    before=lifecycle.snapshot(store.root)
    result=bot.build_live_trade_signal('IC',CLOCK,'close')
    assert result['score']==3.5 and result['valuation_provenance']['mode']=='vip_actual'
    assert result['core_put_target_delta']==.5
    assert result['grid_current']==anchors['IC']['verified_next_grid_units']==0.
    assert result['grid_target']==0.
    assert result['state_anchor_day']==PREVIOUS
    assert result['v14_action']!='HOLD_FAIL_CLOSED'
    assert lifecycle.snapshot(store.root)==before
    # Persist the actual builder result into a temporary hash-chained store.
    # A valid synthetic sibling signal permits unified commit without bypassing
    # any ledger validator. T+1 intent remains intent, not a fabricated fill.
    sibling=lifecycle.close_evidence(ledger._signal('IM',anchors['IM'],DAY))
    sibling['v14_opening_events']=[]
    result_record=store.append_confirmed_signals(record,{'IC':result,'IM':sibling})
    reopened=state.StateStore(store.root).load_latest()
    assert reopened['digest']==result_record['digest']
    assert reopened['sequence']==record['sequence']+1
    assert reopened['signals']['IC']['score']==3.5
    assert reopened['signals']['IC']['valuation_provenance']['mode']=='vip_actual'
    assert reopened['signals']['IC']['core_put_target_delta']==.5


def test_current_day_snapshot_cannot_relabel_historical_valuation(monkeypatch,tmp_path):
    valuation_input(monkeypatch,tmp_path,snapshot())
    historical=date(2026,10,20)
    result=valuation.resolve('IC',historical,CLOCK,price=7800.,frozen=bot.FROZEN['IC'],anchor_day=bot.DATA_CUTOFF)
    assert result['provenance']['mode']=='proxy'
    assert result['provenance']['real_data_date']==DAY.isoformat()
    assert result['provenance']['market_date']==historical.isoformat()


@pytest.mark.parametrize('product',['IC','IM'])
def test_historical_replay_with_correct_available_snapshot_still_uses_actual_valuation(product,monkeypatch,tmp_path):
    historical=PREVIOUS
    live_sources(monkeypatch,tmp_path,product,proxy_score=-100.,vip=snapshot(historical))
    frame=full_frame(product,historical)
    monkeypatch.setattr(bot,'fetch_ohlcv_history',lambda _:frame.copy())
    clock=datetime(2026,10,20,18,tzinfo=bot.BEIJING)
    with bot.historical_replay(historical):
        value=bot.live_proxy(product,clock)
    assert value['history_date']==historical
    assert value['valuation_provenance']['mode']=='vip_actual'
    assert value['valuation_provenance']['market_date']==historical.isoformat()
    assert value['score']==3.5


@pytest.mark.parametrize('saved_mode',['proxy','vip_actual'])
def test_stored_v14_report_never_relabels_from_current_environment(saved_mode,monkeypatch,tmp_path):
    import run_ic_im_v1_4_github_digest as runner
    valuation_input(monkeypatch,tmp_path,snapshot() if saved_mode=='proxy' else None)
    def forbidden(*args,**kwargs):
        raise AssertionError('stored report must not resolve new valuation inputs')
    monkeypatch.setattr(valuation,'resolve',forbidden)
    provenance=dict(mode=saved_mode,reason='synthetic saved source',
                    real_data_date=DAY.isoformat() if saved_mode=='vip_actual' else None,
                    proxy_anchor_date=bot.DATA_CUTOFF.isoformat())
    record=dict(verified_day=DAY.isoformat(),signals={p:{'valuation_provenance':deepcopy(provenance)}
                                                   for p in ['IC','IM']})
    before=deepcopy(record)
    report=runner.render_stored_close_report(record)
    assert record==before
    if saved_mode=='proxy':
        assert '代理估值' in report and '真实PE/PB：乐咕VIP' not in report
    else:
        assert '真实PE/PB：乐咕VIP' in report


def test_historical_unbound_older_signal_cannot_advance_current_ledger(monkeypatch,tmp_path):
    store,record=lifecycle.recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(record)
    signals={p:lifecycle.close_evidence(ledger._signal(p,anchors[p],PREVIOUS)) for p in ['IC','IM']}
    for value in signals.values():
        value['market_date']=PREVIOUS
        value['v14_opening_events']=[]
    before=lifecycle.snapshot(store.root)
    with pytest.raises(RuntimeError,match='逐交易日推进'):
        store.append_confirmed_signals(record,signals)
    assert lifecycle.snapshot(store.root)==before


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('kind',['stale_day','skipped_unverified_day'])
def test_production_date_boundary_prevents_unanchored_legacy_current_state(product,kind,monkeypatch):
    anchor=deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
    anchor['last_verified_day']=PREVIOUS
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,product,anchor)
    market_day=PREVIOUS if kind=='stale_day' else date(2026,10,22)
    clock=CLOCK if kind=='stale_day' else datetime(2026,10,22,18,tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError,match='已过期|尚未可靠重放'):
        bot._validate_signal_market_date(product,market_day,clock,'close')
