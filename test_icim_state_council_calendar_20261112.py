"""Accepted public-holiday basis; synthetic signal shapes never represent market quotes."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pytest

import ic_im_state_council_calendar_v1 as cal
import poe_ic_im_mainline_v1_4_bot as bot

NOW = datetime(2026, 11, 12, 18, tzinfo=bot.BEIJING)
WEEKDAY_CLOSURES = {
    date(2027,1,1), date(2027,2,5), *[date(2027,2,d) for d in range(8,13)],
    date(2027,4,5), *[date(2027,5,d) for d in range(3,6)], date(2027,6,9),
    date(2027,9,15), date(2027,10,1), *[date(2027,10,d) for d in range(4,8)],
}


def test_all_2027_dates_match_weekends_plus_published_holidays():
    definition = cal.load()
    open_count = 0
    with bot.runtime_clock(NOW):
        for offset in range(365):
            day = date(2027,1,1)+timedelta(days=offset)
            expected = day.weekday() < 5 and day not in WEEKDAY_CLOSURES
            assert definition.trading_day(day) is expected
            assert bot._is_exchange_trading_day(day) is expected
            open_count += expected
    assert open_count == 243


@pytest.mark.parametrize("day", [date(2027,1,31),date(2027,2,14),date(2027,5,8),date(2027,9,26),date(2027,10,9)])
def test_makeup_working_weekends_stay_closed(day):
    with bot.runtime_clock(NOW):
        assert day in cal.load().makeup_workdays
        assert bot._is_exchange_trading_day(day) is False


@pytest.mark.parametrize("start,expected", [
    (date(2027,1,1),date(2027,1,4)), (date(2027,2,5),date(2027,2,15)),
    (date(2027,4,3),date(2027,4,6)), (date(2027,5,1),date(2027,5,6)),
    (date(2027,6,9),date(2027,6,10)), (date(2027,9,15),date(2027,9,16)),
    (date(2027,10,1),date(2027,10,8)),
])
def test_holiday_roll_forward(start, expected):
    with bot.runtime_clock(NOW):
        assert bot._roll_forward_exchange_day(start,require_official=True) == expected


def test_year_boundary_and_latest_completed_session():
    with bot.runtime_clock(NOW):
        assert bot._roll_forward_exchange_day(date(2027,1,1),require_official=True)==date(2027,1,4)
        clock=datetime(2027,1,4,8,tzinfo=bot.BEIJING)
        assert bot._latest_completed_exchange_day(clock)==date(2026,12,31)
        assert bot._third_friday(2027,3)==date(2027,3,19)
        assert bot._fourth_wednesday(2027,3)==date(2027,3,24)


def test_fear_fallback_uses_the_accepted_cross_year_previous_session():
    import ic_im_fear_grid_v1 as fear_grid
    with bot.runtime_clock(NOW):
        assert fear_grid.previous_exchange_session(date(2027,1,4))==date(2026,12,31)


def test_2026_published_holidays_reproduce_existing_exchange_calendar():
    periods=[(date(2026,1,1),date(2026,1,3)),(date(2026,2,15),date(2026,2,23)),
             (date(2026,4,4),date(2026,4,6)),(date(2026,5,1),date(2026,5,5)),
             (date(2026,6,19),date(2026,6,21)),(date(2026,9,25),date(2026,9,27)),
             (date(2026,10,1),date(2026,10,7))]
    with bot.runtime_clock(NOW):
        for offset in range(365):
            day=date(2026,1,1)+timedelta(days=offset)
            expected=day.weekday()<5 and not any(start<=day<=end for start,end in periods)
            assert bot._is_exchange_trading_day(day) is expected
        assert bot._is_exchange_trading_day(date(2024,2,9)) is False
        assert bot._third_friday(2026,6)==date(2026,6,22)


def future_signal():
    return {'market_date':date(2026,11,20),'next_trade_date':date(2026,11,23),
            'core_current':'IM2612','core_target':'IM2612',
            'put_target_contract':'MO2703-P-8000','core_put_target_contract':'MO2703-P-8000',
            'core_put_target_qty_normalized':1.5}


def attach_authority(signal):
    rows=bot.require_signal_calendar('IM',signal)
    assert rows
    signal['calendar_source_evidence']={'policy_revision':cal.REVISION,
                                      'exchange_notice_verified':False,'sources':rows}


def test_accepted_calendar_does_not_masquerade_as_exchange_confirmation():
    with bot.runtime_clock(NOW):
        signal=future_signal(); attach_authority(signal)
        bot.validate_calendar_authority('IM',signal)
        info=signal['calendar_source_evidence']
        assert info['exchange_notice_verified'] is False
        assert all(row['accepted_for_research'] is True and row['source']=='state_council_derived' for row in info['sources'])
        assert all(row['source_capture_kind']=='search_service_retrieved_text' for row in info['sources'])
        assert 2027 not in bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS


@pytest.mark.parametrize('corruption',['missing','official','hash','date','policy'])
def test_authority_cannot_be_removed_or_forged(corruption):
    with bot.runtime_clock(NOW):
        signal=future_signal(); attach_authority(signal)
        if corruption=='missing':signal.pop('calendar_source_evidence')
        elif corruption=='official':signal['calendar_source_evidence']['exchange_notice_verified']=True
        elif corruption=='hash':signal['calendar_source_evidence']['sources'][0]['source_text_sha256']='0'*64
        elif corruption=='date':signal['calendar_source_evidence']['sources'][0]['date']='2027-03-20'
        else:signal['calendar_source_evidence']['policy_revision']='unknown'
        with pytest.raises(RuntimeError,match='日历来源'):
            bot.validate_calendar_authority('IM',signal)


@pytest.mark.parametrize('file_name',['data','notice'])
def test_source_tampering_is_rejected_even_after_a_successful_load(monkeypatch,tmp_path,file_name):
    data=tmp_path/'calendar.json';notice=tmp_path/'notice.txt'
    data.write_bytes(cal.DATA_PATH.read_bytes());notice.write_bytes(cal.NOTICE_PATH.read_bytes())
    # Keep the approved basename; both files remain within the disposable tree.
    notice=notice.with_name(cal.NOTICE_PATH.name);notice.write_bytes(cal.NOTICE_PATH.read_bytes())
    monkeypatch.setattr(cal,'DATA_PATH',data);monkeypatch.setattr(cal,'NOTICE_PATH',notice)
    cal.load()
    target=data if file_name=='data' else notice
    target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(cal.CalendarEvidenceError,match='SHA-256'):
        cal.load()
    with bot.runtime_clock(NOW), pytest.raises(bot.CalendarCoverageError):
        bot._require_official_exchange_calendar(date(2027,3,1),'tamper negative')


def test_missing_source_is_not_replaced_by_weekday_guess(monkeypatch,tmp_path):
    monkeypatch.setattr(cal,'DATA_PATH',tmp_path/'missing.json')
    with bot.runtime_clock(NOW),pytest.raises(bot.CalendarCoverageError):
        bot._require_official_exchange_calendar(date(2027,3,1),'missing negative')


def test_pre_adoption_replay_preserves_old_unknown_calendar_behavior():
    with bot.runtime_clock(datetime(2026,11,11,18,tzinfo=bot.BEIJING)):
        with pytest.raises(bot.CalendarCoverageError):
            bot._require_official_exchange_calendar(date(2027,3,1),'before policy')
    token=bot._HISTORICAL_REPLAY_DAY.set(date(2026,10,22))
    try:
        with bot.runtime_clock(NOW),pytest.raises(bot.CalendarCoverageError):
            bot._require_official_exchange_calendar(date(2027,3,1),'old replay')
    finally:bot._HISTORICAL_REPLAY_DAY.reset(token)


def test_state_validator_uses_the_signal_day_not_today():
    signal=future_signal();signal['market_date']=date(2026,11,11)
    with bot.runtime_clock(NOW),pytest.raises(bot.CalendarCoverageError):
        bot.validate_calendar_authority('IM',signal)


def test_unknown_2028_stays_closed_to_signal_adoption():
    with bot.runtime_clock(NOW),pytest.raises(bot.CalendarCoverageError):
        bot._require_official_exchange_calendar(date(2028,3,1),'unknown year')


def test_accepted_model_expiry_does_not_force_diagnostic_mode():
    context={'expiries':[],'failures':[]}
    token=bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        with bot.runtime_clock(NOW):bot._require_model_expiry_calendar(date(2027,3,24),'accepted expiry')
        assert context=={'expiries':[],'failures':[]}
    finally:bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
