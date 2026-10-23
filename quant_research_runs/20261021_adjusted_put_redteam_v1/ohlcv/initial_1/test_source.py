"""Independent OHLCV boundary audit; synthetic bars, no production stores."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT/'quant_research_runs/20261021_adjusted_put_redteam_v1/ohlcv'
DAY = date(2026,10,21)
CLOCK = datetime(2026,10,21,15,1,tzinfo=bot.BEIJING)


@pytest.fixture(scope='module', autouse=True)
def bind_sources():
    phase = os.environ.get('ICIM_AUDIT_STAGE','ohlcv/manual').split('/')[-1]
    target = AUDIT/phase
    target.mkdir(parents=True,exist_ok=True)
    paths = [Path(bot.__file__),Path(__file__),ROOT/'ic_im_put_execution_v1.py',
             ROOT/'ic_im_adjusted_put_migration_v1.py']
    def hashes():
        return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    with (target/'source_before.json').open('x',encoding='utf-8') as handle:
        json.dump(before,handle,indent=2)
    yield
    after = hashes()
    with (target/'source_after.json').open('x',encoding='utf-8') as handle:
        json.dump(after,handle,indent=2)
    assert before == after, 'source changed during independent audit run'


def bars(last='2026-10-20'):
    dates = pd.date_range('2026-10-19',last,freq='D')
    return pd.DataFrame(dict(open=7000.,high=7100.,low=6900.,close=7050.,volume=100_000_000.),index=dates)


def small_valid_history(monkeypatch,frame):
    # Freeze a small synthetic warmup only; the real calendar/freshness code runs.
    cutoff = date(2026,10,20)
    fixed = frame.loc[frame.index.date <= cutoff].index
    payload = '\n'.join(fixed.strftime('%Y-%m-%d'))+'\n'
    meta = deepcopy(bot.V13_HISTORY_DATE_INDEX)
    for product in ('IC','IM'):
        meta[product] = (fixed[0].date(),len(fixed),hashlib.sha256(payload.encode()).hexdigest())
    monkeypatch.setattr(bot,'DATA_CUTOFF',cutoff)
    monkeypatch.setattr(bot,'V13_HISTORY_DATE_INDEX',meta)


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('mode',['intraday','close'])
def test_public_builder_waits_without_touching_pending_anchor(product,mode,monkeypatch):
    frame = bars()
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    monkeypatch.setattr(bot,'fetch_ohlcv_history',lambda _:frame)
    def forbidden(*args,**kwargs):
        raise AssertionError('waiting must precede price replacement or opening-plan projection')
    monkeypatch.setattr(bot,'fetch_live_price_quote',forbidden)
    monkeypatch.setattr(bot,'_v14_ordinary_open_evidence',forbidden)
    with pytest.raises(bot.DailyOHLCVNotReady) as error:
        bot.build_live_trade_signal(product,now=CLOCK,mode=mode)
    assert error.value.status == 'waiting_for_completed_daily_ohlcv'
    assert bot.LIVE_CONTINUATION_ANCHOR == before
    assert frame.index[-1].date() == date(2026,10,20)


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('zone',[bot.BEIJING,timezone.utc,ZoneInfo('Asia/Tokyo')])
def test_same_instant_has_same_after_close_wait_status(product,zone,monkeypatch):
    frame = bars()
    small_valid_history(monkeypatch,frame)
    clock = CLOCK.astimezone(zone)
    with pytest.raises(bot.DailyOHLCVNotReady):
        bot._validate_v13_ohlcv(product,frame,clock)


@pytest.mark.parametrize('zone',[bot.BEIJING,timezone.utc,ZoneInfo('Asia/Tokyo')])
def test_public_builder_passes_a_beijing_clock_to_implementation(zone,monkeypatch):
    clocks = []
    def implementation(product,clock,mode):
        clocks.append(clock)
        return {'close_confirmed':False}
    monkeypatch.setattr(bot,'_build_live_trade_signal',implementation)
    bot.build_live_trade_signal('IC',now=CLOCK.astimezone(zone),mode='close')
    assert clocks == [CLOCK]
    assert clocks[0].tzinfo == bot.BEIJING


@pytest.mark.parametrize('zone',[bot.BEIJING,timezone.utc,ZoneInfo('Asia/Tokyo')])
def test_same_preclose_instant_cannot_enter_close_builder(zone,monkeypatch):
    clock = datetime(2026,10,21,14,59,tzinfo=bot.BEIJING).astimezone(zone)
    def forbidden(*args,**kwargs):
        raise AssertionError('close implementation must not run before Beijing close')
    monkeypatch.setattr(bot,'_build_live_trade_signal',forbidden)
    with pytest.raises(RuntimeError,match='尚未收盘'):
        bot.build_live_trade_signal('IC',now=clock,mode='close')


@pytest.mark.parametrize('product',['IC','IM'])
def test_complete_daily_bar_remains_usable(product,monkeypatch):
    frame = bars('2026-10-21')
    small_valid_history(monkeypatch,frame)
    checked = bot._validate_v13_ohlcv(product,frame,CLOCK)
    pd.testing.assert_frame_equal(checked,frame)


@pytest.mark.parametrize('product',['IC','IM'])
def test_known_placeholder_cannot_be_promoted_to_completed_daily_bar(product,monkeypatch):
    # This is a defensive direct-adapter probe; current live_proxy creates its
    # placeholders AFTER validation on a copy, so no production reachability
    # claim is made merely from accepting this injected marked frame.
    frame = bars('2026-10-21')
    frame['realtime_volume_placeholder'] = [False,False,True]
    small_valid_history(monkeypatch,frame)
    with pytest.raises(RuntimeError):
        bot._validate_v13_ohlcv(product,frame,CLOCK)


def test_daily_wait_is_not_retried_as_a_transport_failure(monkeypatch):
    calls = []
    def wait(*args,**kwargs):
        calls.append(args)
        raise bot.DailyOHLCVNotReady('waiting for actual daily volume')
    monkeypatch.setattr(bot,'build_live_trade_signal',wait)
    with pytest.raises(bot.DailyOHLCVNotReady):
        bot.build_live_signal_with_transport_retry('IC','close',20.)
    assert len(calls) == 1


@pytest.mark.parametrize('product',['IC','IM'])
def test_report_keeps_waiting_product_out_of_observer(product,monkeypatch):
    text,observed = [],[]
    class Message:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def write(self,value):text.append(str(value))
    monkeypatch.setattr(bot,'_sm',lambda:Message())
    monkeypatch.setattr(bot,'_now_beijing',lambda:CLOCK)
    monkeypatch.setattr(bot,'fetch_ohlcv_history',lambda _:bars())
    monkeypatch.setattr(bot,'_write_last_verified_snapshot',lambda *_:None)
    monkeypatch.setattr(bot,'_SIGNAL_OBSERVER',lambda *args:observed.append(args))
    bot.ICIMMainlinesBot()._handle_signal((product,),'close')
    rendered = ''.join(text)
    assert not observed
    assert '等待当日完整日线' in rendered
    assert '新目标尚未确认' in rendered
    assert '下一交易日确认目标' not in rendered
