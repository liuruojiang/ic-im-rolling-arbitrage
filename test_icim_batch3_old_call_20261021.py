"""IM legacy-Call diagnostics; synthetic restored holdings, no production writes."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as seed

DAY = date(2026,10,21)
PREVIOUS = date(2026,10,20)
CLOCK = datetime(2026,10,21,15,40,tzinfo=bot.BEIJING)
CALL = 'MO2612-C-9000'
EXPIRY = date(2026,12,18)
ROOT = Path(__file__).resolve().parent
AUDIT = ROOT/'quant_research_runs/20261021_external_review_batch3_v1/old_call'


@pytest.fixture(scope='module',autouse=True)
def source_binding():
    phase = os.environ.get('ICIM_AUDIT_STAGE','old_call/manual').split('/')[-1]
    target = AUDIT/phase
    target.mkdir(parents=True,exist_ok=True)
    paths = [Path(bot.__file__),Path(policy.__file__),Path(state.__file__),Path(__file__),Path(seed.__file__)]
    def hashes():
        return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    with (target/'source_before.json').open('x',encoding='utf-8') as handle:
        json.dump(before,handle,indent=2)
    yield
    after = hashes()
    with (target/'source_after.json').open('x',encoding='utf-8') as handle:
        json.dump(after,handle,indent=2)
    assert before == after, 'Source changed during old Call independent tests'


def record(*,held=True):
    result = seed._record()
    result.update(verified_day=PREVIOUS.isoformat())
    for anchor in result['products'].values():
        anchor.update(last_verified_day=PREVIOUS.isoformat(),verified_grid_entry_source='none',
                      verified_next_grid_entry_source='none')
    result['products']['IM'].update(
        verified_call_contracts_normalized=-1. if held else 0.,
        verified_call_contract=CALL if held else None,
        verified_call_expiry=EXPIRY.isoformat() if held else None,
        verified_call_strike=9000. if held else None)
    result['digest'] = state._digest(result)
    state._validate_record(result)
    return result


def wire(monkeypatch,*,held=True,bid=99.,ask=101.,last=100.,position=20.):
    prepared = record(held=held)
    anchors = state.anchors_from_record(prepared)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',deepcopy(anchors))
    template = json.loads(seed.FIXTURE.read_text(encoding='utf-8'))['signals']['IM']
    live = dict(template,history_date=DAY,price=8000.,score=3.,momentum_120=-.1,
        momentum_current_weight=0.,momentum_next_weight=0.,momentum_signal_date=DAY,
        grid_current_units=0.,grid_target_units=0.,
        valuation_provenance={'audit':'synthetic'},v13_parent_puts_per_full_core=3,
        v13_current_parent_puts=3,v13_put_target_qty_normalized=1.5,
        absolute_valuation_tier=0,absolute_valuation_tier_label='synthetic absolute',
        relative_valuation_tier=0,relative_valuation_tier_label='synthetic relative',
        valuation_puts_per_full_core=0,mom120_floor_puts_per_full_core=3,
        core_put_driver='synthetic negative MOM120',momentum_volume_placeholder=False,
        momentum_score=-1.,momentum_abs20=-.1,momentum_volume_ratio=1.,
        momentum_volume_pass=True,momentum_score_hot=False,momentum_base_nav=1.,
        momentum_base_dd=0.,momentum_nav_defense=False)
    monkeypatch.setattr(bot,'live_proxy',lambda *a:deepcopy(live))
    monkeypatch.setattr(bot.fear_grid,'fetch_score',lambda *a:dict(
        fear_greed_index=40.,fear_data_status='same_day_post_close',fear_data_date=DAY,
        fear_page_updated_at=DAY.isoformat()+'T15:01:00+08:00',fear_csv_sha256='a'*64,
        fear_retrieved_at=DAY.isoformat()+'T15:30:00+08:00',fear_source_url=bot.fear_grid.CSV_URL))
    monkeypatch.setattr(bot.fear_grid,'fetch_jiu_reference',lambda *a:{})
    futures = pd.DataFrame([dict(instrument='IM'+month,lastprice=8000.,
        bprice=7999.,sprice=8001.,volume=1000.,position=2000.)
        for month in ['2611','2612','2703','2706']])
    futures.attrs.update(source='synthetic_full_future_chain',source_date=DAY,
                         listed_instruments=list(futures.instrument))
    mo = pd.DataFrame([
        dict(instrument='MO2612-P-7200',lastprice=200.,bprice=199.,sprice=201.,
             volume=10.,position=20.),
        dict(instrument=CALL,lastprice=last,bprice=bid,sprice=ask,volume=10.,position=position),
    ])
    mo.attrs.update(source='synthetic_prepared_option_observations',source_date=DAY,
                    listed_instruments=list(mo.instrument))
    monkeypatch.setattr(bot,'fetch_cffex_quotes',lambda product,*a:mo if product=='MO' else futures)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *a:.02)
    monkeypatch.setattr(bot,'_v14_im_short_put_candidate',lambda *a,**kw:{'tradable':False})
    # These forbidden calls prove the NO_CALL maintenance route does not request
    # a new entry or rescue candidate while old exposure is being closed.
    def forbidden(*a,**k):
        raise AssertionError('NO_CALL must not select a new Call')
    monkeypatch.setattr(bot,'select_im_call_d10',forbidden)
    monkeypatch.setattr(bot,'select_im_call_rescue',forbidden)
    return anchors,mo


def test_public_builder_restored_old_call_closes_with_valid_two_sided_quote(monkeypatch):
    anchors,_ = wire(monkeypatch)
    signal = bot.build_live_trade_signal('IM',now=CLOCK,mode='close')
    assert signal['call_current_contract'] == CALL
    assert signal['call_action'] == 'CLOSE_CALL'
    assert signal['call_target_contract'] is None
    assert signal['call_target_qty_normalized'] == 0.
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors


@pytest.mark.parametrize('bid',[0.,float('nan')])
def test_current_public_legacy_exit_gate_requires_bid_despite_positive_ask(monkeypatch,bid):
    # This documents an existing dormant restriction, not a claim that any local
    # recorded position is blocked or that the current execution rule is fixed.
    anchors,_ = wire(monkeypatch,bid=bid)
    with pytest.raises(RuntimeError,match='IM Call.*双边有效报价'):
        bot.build_live_trade_signal('IM',now=CLOCK,mode='close')
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors


def test_public_empty_call_holding_does_not_need_a_bid_for_an_unheld_call(monkeypatch):
    anchors,_ = wire(monkeypatch,held=False,bid=0.,ask=101.)
    signal = bot.build_live_trade_signal('IM',now=CLOCK,mode='close')
    assert signal['call_action'] == 'HOLD'
    assert signal['call_current_contract'] is None
    assert signal['call_target_contract'] is None
    assert signal['call_target_qty_normalized'] == 0.
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors


def test_source_chain_accepts_positive_ask_and_zero_bid_before_legacy_exit_gate():
    rows = []
    for month in bot._expected_mo_months(DAY):
        for kind in ['C','P']:
            for strike in [7200,8000,9000]:
                contract = month+'-'+kind+'-'+str(strike)
                rows.append(dict(instrument=contract,lastprice=100.,volume=10.,position=20.,
                    bprice=0. if contract == CALL else 99.,sprice=101.,source_date=DAY,
                    source_time='15:00:00'))
    checked = bot._validate_quote_frame(pd.DataFrame(rows),'MO',CLOCK,'synthetic_adapter_control')
    selected = bot._quote_row(checked,CALL)
    assert selected is not None and selected['bprice'] == 0. and selected['sprice'] == 101.
    with pytest.raises(RuntimeError,match='双边有效报价'):
        bot._require_existing_leg_quote('IM Call',CALL,selected,'CLOSE_CALL')


@pytest.mark.parametrize('held',[True,False])
def test_persistent_input_gate_rejects_reintroduced_call_after_public_zero_target(monkeypatch,held):
    wire(monkeypatch,held=held)
    signal = bot.build_live_trade_signal('IM',now=CLOCK,mode='close')
    state.validate_im_option_values(signal)
    forged = dict(signal,call_target_contract='MO2612-C-9500',call_target_qty_normalized=-1.,
                  call_target_expiry=EXPIRY,call_target_strike=9500.,call_action='OPEN_CALL')
    with pytest.raises(RuntimeError,match='不得卖Call'):
        state.validate_im_option_values(forged)


@pytest.mark.parametrize('ask',[0.,float('nan'),-1.])
def test_invalid_ask_never_supports_old_call_buyback(monkeypatch,ask):
    wire(monkeypatch,bid=99.,ask=ask)
    with pytest.raises(RuntimeError):
        bot.build_live_trade_signal('IM',now=CLOCK,mode='close')


def test_known_call_expiry_does_not_require_a_buyback_quote_after_expiration():
    assert not bot._im_call_finished(CALL,EXPIRY,EXPIRY,False)
    assert bot._im_call_finished(CALL,EXPIRY,EXPIRY,True)
    assert bot._im_call_finished(CALL,EXPIRY,date(2026,12,21),False)


@pytest.mark.parametrize('action',['OPEN_CALL','RESCUE_NEXT_LISTED'])
def test_policy_cannot_reopen_or_roll_call_after_no_call_date(action):
    signal = dict(product='IM',market_date=DAY,next_trade_date=date(2026,10,22),
        close_confirmed=True,valuation_tier=1,valuation_puts_per_full_core=0,
        momentum_120=-.1,momentum_next_weight=0.,option_monthly_reset_due=False,
        core_units_current=.5,core_units_target=.5,total_units_current=.5,total_units_target=.5,
        momentum_put_target_qty_normalized=0.,call_current_contract=CALL,call_has_position=True,
        call_action=action,call_target_contract='MO2612-C-9500',
        call_target_qty_normalized=-1.,call_target_expiry=EXPIRY,call_target_strike=9500.)
    result = policy.apply_policy('IM',signal,policy.default_extension('IM'),candidate={'tradable':False})
    assert result['call_action'] == 'CLOSE_CALL'
    assert result['call_target_contract'] is None
    assert result['call_target_qty_normalized'] == 0.
