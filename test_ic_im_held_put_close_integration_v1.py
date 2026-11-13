"""Held-only close fallback integration; frozen real inputs and hard refusals."""
from copy import deepcopy
from datetime import date, datetime
from unittest.mock import Mock

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
from test_ic_im_held_put_close_v1 import valid_inputs


def setup_case(monkeypatch, *, day=date(2026, 11, 13), closed=True, monthly=False):
    inputs = valid_inputs()
    clock = inputs['observed_at']
    monkeypatch.setattr(bot, '_now_beijing', lambda: clock)
    anchor = {**inputs['anchor'], 'verified_core_put_delta': 0.25}
    signal = dict(market_date=day, close_confirmed=closed, option_monthly_reset_due=monthly,
                  core_put_target_delta=0.25, put_target_momentum_qty=0)
    raw = pd.DataFrame([dict(contract=anchor['v14_core_put_contract'], security_id=anchor['v14_core_put_security_id'],
                            strike=6.5, last=0.2703, source_stamp=clock.replace(hour=0, minute=0, second=0))])
    stamp = dict(source='新浪财经期权详报价（上交所回退）', date='20261113', time='150000')
    monkeypatch.setattr(bot, '_fetch_510500_chain_for_day', lambda *args: (raw, stamp))
    checked = bot.held_put_close.validate_held_put_close(**inputs)
    frame = pd.DataFrame([checked['row']])
    frame.attrs['held_close_evidence'] = checked['evidence']
    daily_stamp = dict(date='2026-11-13', time=None, quote_kind='daily_close',
                       source=checked['evidence']['source'], observed_at=clock.isoformat())
    fallback = Mock(return_value=(frame, daily_stamp, inputs))
    monkeypatch.setattr(bot, '_fetch_held_ic_put_daily_close_for_day', fallback)
    return signal, anchor, raw, stamp, fallback


def test_actual_midnight_held_row_uses_daily_close_without_claiming_realtime_iv(monkeypatch):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_called_once()
    assert signal['v14_core_put_qty'] == 15
    assert signal['v14_core_put_contract'] == '510500P2612M06500'
    assert signal['iv_monitor_evidence']['quote_time'] is None
    assert signal['iv_monitor_evidence']['iv_monitor_eligible'] is False
    assert '20分钟' in signal['v14_core_put_close_evidence']['primary_failure']


@pytest.mark.parametrize('changes', [{'close_confirmed': False}, {'option_monthly_reset_due': True},
                                  {'market_date': date(2026, 11, 12)}])
def test_nonapplicable_signals_keep_original_refusal(monkeypatch, changes):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    signal.update(changes)
    with pytest.raises(RuntimeError):
        bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_not_called()


@pytest.mark.parametrize('error_type', [bot.expiry_evidence.ExpiryEvidenceConflict, bot.signal_evidence.EvidenceUnavailable])
def test_existing_hard_evidence_rejections_never_fall_back(monkeypatch, error_type):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    monkeypatch.setattr(bot, '_fetch_510500_chain_for_day', Mock(side_effect=error_type('original conflict')))
    with pytest.raises(error_type):
        bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_not_called()


def test_positive_security_identity_conflict_is_not_hidden(monkeypatch):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    raw.loc[0, 'security_id'] = '10012148'
    with pytest.raises(bot.held_put_close.HeldPutCloseConflict):
        bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_not_called()


def test_valid_realtime_primary_does_not_acquire_daily_data(monkeypatch):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    raw.loc[0, 'source_stamp'] = datetime.fromisoformat('2026-11-13T15:00:00+08:00')
    bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_not_called()
    assert signal['iv_monitor_evidence']['quote_time'] == '150000'


@pytest.mark.parametrize('qty', [0, -1, True, float('inf'), float('nan')])
def test_ineligible_held_anchors_cannot_start_network(monkeypatch, qty):
    data = valid_inputs()
    monkeypatch.setattr(bot, '_now_beijing', lambda: data['observed_at'])
    data['anchor']['v14_core_put_qty'] = qty
    assert not bot._held_ic_put_close_eligible(data['signal_day'], data['anchor'])


def test_daily_close_is_explicitly_ineligible_for_iv_monitor(monkeypatch):
    signal, anchor, raw, stamp, fallback = setup_case(monkeypatch)
    bot._v14_ic_core_overlay(signal, anchor)
    signal.update(etf_price=6.4)
    result = bot.build_iv_warning('IC', signal, {}, None, valid_inputs()['observed_at'])
    assert '日线收盘价' in str(result)
