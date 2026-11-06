"""Independent qualification adversaries; all provider inputs are synthetic."""
from copy import deepcopy
import pytest
import poe_ic_im_v1_4_state as state
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_new_put_calendar_partial_20261106 import wire_new_momentum,INTRADAY_CLOCK


def partial(monkeypatch):
    wire_new_momentum(monkeypatch,intraday=True)
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: pytest.fail('No live providers in audit'))
    return bot.build_live_trade_signal('IM',INTRADAY_CLOCK,'intraday')


def test_real_partial_quote_evidence_binds_current_contract_and_cutoff(monkeypatch):
    signal=partial(monkeypatch)
    before=deepcopy(signal)
    state.validate_calendar_partial_publication(signal,'IM')
    assert signal==before
    assert signal['future_quote_evidence']['contract']==signal['core_current']
    assert signal['future_quote_evidence']['source_snapshot_at']=='2026-11-06T14:35:00+08:00'
    assert signal['future_quote_evidence']['collected_at'].startswith('2026-11-06T14:40:')


@pytest.mark.parametrize('field,value',[
 ('future_quote_date','2026-11-05'),('future_quote_source',''),
 ('future_quote_evidence',None),('momentum_put_target_strike',8200.),
 ('momentum_put_target_expiry','2027-01-15'),('momentum_put_target_price',200.),
 ('momentum_put_target_bid',199.),('momentum_put_target_ask',201.),
 ('momentum_put_target_delta',-.5),('momentum_put_quote',{'contract':'MO2701-P-8200'}),
 ('momentum_put_pricing_evidence',{'price':200.}),('momentum_put_target_security_id','forged'),
])
def test_bad_source_or_unconfirmed_option_fields_never_qualify(monkeypatch,field,value):
    signal=partial(monkeypatch);signal[field]=value
    with pytest.raises((RuntimeError,ValueError,TypeError)):
        state.validate_calendar_partial_publication(signal,'IM')


@pytest.mark.parametrize('field,value',[
 ('source_date','2026-11-05'),('row_source_date','2026-11-05'),
 ('source','wrong-provider'),('contract','IM2703'),('lastprice',1.),
 ('source_snapshot_kind','mtime'),('source_snapshot_at','2026-11-06T14:35:00'),
 ('source_snapshot_at','2026-11-05T14:35:00+08:00'),
 ('source_snapshot_at','2026-11-06T14:45:00+08:00'),
 ('collected_at','2026-11-06T15:00:00+08:00'),('collected_at','2026-11-06T14:40:00'),
 ('collected_at','2026-11-05T14:40:00+08:00'),
])
def test_current_future_snapshot_and_actual_collection_are_independent_gates(monkeypatch,field,value):
    signal=partial(monkeypatch);signal['future_quote_evidence'][field]=value
    with pytest.raises((RuntimeError,ValueError,TypeError)):
        state.validate_calendar_partial_publication(signal,'IM')


def test_snapshot_after_cutoff_cannot_be_hidden_by_later_report(monkeypatch):
    signal=partial(monkeypatch)
    signal['future_quote_evidence'].update(source_snapshot_at='2026-11-06T15:00:01+08:00',
                                         collected_at='2026-11-06T15:00:02+08:00')
    with pytest.raises(RuntimeError):
        state.validate_calendar_partial_publication(signal,'IM')


def test_producer_clears_injected_new_put_price_fields_after_policy(monkeypatch):
    wire_new_momentum(monkeypatch,intraday=True)
    original=bot._apply_v14_live_policy
    def selected(*a,**k):
        result=original(*a,**k)
        result.update(momentum_put_target_strike=8200.,momentum_put_target_delta=-.5,
                      momentum_put_quote={'price':200.},momentum_put_pricing_evidence={'contract':'MO2701-P-8200'})
        return result
    monkeypatch.setattr(bot,'_apply_v14_live_policy',selected)
    signal=bot.build_live_trade_signal('IM',INTRADAY_CLOCK,'intraday')
    for key in ('momentum_put_target_strike','momentum_put_target_delta','momentum_put_quote','momentum_put_pricing_evidence'):
        assert signal[key] is None
    state.validate_calendar_partial_publication(signal,'IM')
