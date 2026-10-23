"""Independent historical-path audit; dates/markets are synthetic offline fixtures."""
from copy import deepcopy
from datetime import date

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round3_recovery_audit_20261021 as seed
import test_icim_round4_recovery_20261021 as r4


def test_historical_builder_without_original_package_never_uses_live_free_etf(tmp_path, monkeypatch):
    """A new live fallback must not leak into an ordinary dated historical query."""
    day, previous = date(2026, 10, 21), date(2026, 10, 20)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.setenv('ICIM_STATE_DIR', str(tmp_path / 'import_only'))
    monkeypatch.delenv('ICIM_EVIDENCE_DIR', raising=False)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('historical test escaped to network'))
    monkeypatch.setattr(bot, 'build_iv_warning', lambda *a, **k: {'status': 'synthetic_audit_monitor_omitted'})
    clock = r4.wall(monkeypatch, day)
    store, original = seed.temporary_store(tmp_path / 'state', previous)
    bot.install_runtime_anchors(state.anchors_from_record(original))
    starting_anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    samples = r4.providers(monkeypatch, day)
    calls = []
    def historical_option(contract, security_id, requested):
        calls.append(('option', requested))
        assert requested == day and security_id == '10012099'
        frame, stamp = deepcopy(samples['IC'][4]['2612'])
        assert frame.iloc[0]['contract'] == contract
        return frame, stamp
    def historical_etf(requested):
        calls.append(('etf', requested))
        assert requested == day
        return {'date': str(day), 'time': '150000', 'last': 7.8, 'prev_close': 7.8,
                'source': 'synthetic dated historical ETF'}
    monkeypatch.setattr(bot, 'fetch_sse_existing_put_historical_quote', historical_option)
    monkeypatch.setattr(bot, 'fetch_sse_510500_historical_quote', historical_etf)
    monkeypatch.setattr(bot, 'fetch_510500_quote_with_failover',
                        lambda *a, **k: pytest.fail('live ETF fallback consumed historical replay'))
    with bot.historical_replay(day):
        signal = bot.build_live_trade_signal('IC', clock, 'close')
    assert signal['signal_complete'] and signal['persistence_eligible'], signal.get('diagnostic_reason')
    assert signal['v14_core_put_contract'] == '510500P2612M07500'
    assert signal['etf_price'] == 7.8
    assert ('etf', day) in calls and calls.count(('option', day)) >= 2
    assert store.load_latest()['digest'] == original['digest']
    assert bot.LIVE_CONTINUATION_ANCHOR == starting_anchors
