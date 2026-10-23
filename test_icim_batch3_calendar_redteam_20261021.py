"""Independent calendar consumers; synthetic signals, no state or market writes."""
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path

import pytest
import pandas as pd
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT/'quant_research_runs/20261021_external_review_batch3_v1'


@pytest.fixture(scope='module', autouse=True)
def bind_sources():
    directory = AUDIT/'calendar_redteam_bindings'/os.environ.get('ICIM_AUDIT_STAGE', 'calendar_redteam/manual')
    directory.mkdir(parents=True, exist_ok=True)
    def hashes():
        return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (Path(bot.__file__),Path(state.__file__),Path(__file__))}
    before=hashes()
    with (directory/'source_before.json').open('x',encoding='utf-8') as handle:
        json.dump(before,handle,indent=2)
    yield
    after=hashes()
    with (directory/'source_after.json').open('x',encoding='utf-8') as handle:
        json.dump(after,handle,indent=2)
    assert before == after


def base():
    return dict(market_date=date(2026,10,21),next_trade_date=date(2026,10,22),
                core_current='IM2612',core_target='IM2612')


def plan(contract='MO2612-P-7500',execution=date(2026,10,22)):
    return dict(product='IM',signal_day=date(2026,10,21),execution_day=execution,
                legs={'core':dict(old_contract=None,old_qty=0,new_contract=contract,
                                  new_qty=1,changed=True)})


def test_canonical_open_plan_with_dict_legs_remains_accepted():
    signal=base()
    signal['v14_ordinary_put_open_plan']=plan()
    bot.require_signal_calendar('IM',signal)


@pytest.mark.parametrize('contract,execution',[
    ('MO2703-P-7500',date(2026,10,22)),
    ('MO2612-P-7500',date(2027,1,4)),
])
def test_canonical_ordinary_pending_is_guarded(contract,execution):
    signal=base()
    signal['v14_ordinary_put_pending']=plan(contract,execution)
    with pytest.raises(RuntimeError,match='2027'):
        bot.require_signal_calendar('IM',signal)


def test_im_actual_core_target_field_is_guarded():
    signal=base()
    signal.update(core_put_current_contract='MO2612-P-7500',
                  core_put_target_contract='MO2703-P-7500',
                  core_put_target_qty_normalized=1.)
    with pytest.raises(RuntimeError,match='2027'):
        bot.require_signal_calendar('IM',signal)


@pytest.mark.parametrize('flag',['put_monthly_reset_preview','option_monthly_reset_due'])
def test_im_maintenance_actual_execution_field_is_guarded(flag):
    signal=base()
    signal.update({flag:True,'put_monthly_reset_execution_date':date(2027,1,15)})
    with pytest.raises(RuntimeError,match='2027'):
        bot.require_signal_calendar('IM',signal)


def test_diagnostic_next_core_does_not_block_current_target():
    signal=base()
    signal.update(next_core='IM2703',calendar_coverage_note='unverified far month')
    bot.require_signal_calendar('IM',signal)


def test_dedicated_ic_core_delta_sizing_checks_expiry_before_arithmetic(monkeypatch):
    day=date(2026,10,21)
    stamp=dict(date=day,time='154000',source='synthetic_calendar_boundary')
    chain=pd.DataFrame([dict(contract='510500P2703M07500',security_id='10012099',
        strike=7.5,last=.5,source_stamp=datetime(2026,10,21,15,0,tzinfo=bot.BEIJING))])
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',lambda *a:(chain,stamp))
    monkeypatch.setattr(bot,'_now_beijing',lambda:datetime(2026,10,21,15,40,tzinfo=bot.BEIJING))
    monkeypatch.setattr(bot,'_validate_chain_stamp_matches',lambda *a:None)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *a:.02)
    monkeypatch.setattr(bot,'_implied_volatility',lambda *a:.2)
    monkeypatch.setattr(bot,'_bs_price_delta',lambda *a:(.5,-.25))
    anchor=dict(v14_core_put_contract='510500P2703M07500',
                v14_core_put_security_id='10012099',v14_core_put_qty=5,
                verified_core_put_delta=.25)
    signal=dict(market_date=day,option_monthly_reset_due=False,
                core_put_target_delta=.5,etf_price=7.5,future_last=7500.,
                put_target_momentum_qty=0)
    with pytest.raises(RuntimeError,match='2027'):
        bot._v14_ic_core_overlay(signal,anchor)


def test_public_im_builder_checks_unknown_held_core_expiry(monkeypatch):
    # Actual builder/policy with synthetic restored holding and market rows.
    # The fixture's verified quantities are preserved; only the held month and
    # its matching observed quote are moved across the official-year boundary.
    import test_icim_batch3_old_call_20261021 as legacy
    anchors,quotes=legacy.wire(monkeypatch,held=False)
    bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_put_contract']='MO2703-P-7200'
    quotes.loc[quotes.instrument.eq('MO2612-P-7200'),'instrument']='MO2703-P-7200'
    quotes.attrs['listed_instruments']=list(quotes.instrument)
    with pytest.raises(RuntimeError,match='2027'):
        bot.build_live_trade_signal('IM',now=legacy.CLOCK,mode='close')


@pytest.mark.parametrize('direction,start,closure,unknown',[
    ('forward',date(2026,12,31),date(2026,12,31),'2027'),
    ('backward',date(2026,1,1),date(2026,1,1),'2025'),
])
def test_strict_roll_rechecks_year_after_each_step(monkeypatch,direction,start,closure,unknown):
    # Synthetic closure makes crossing unavoidable; real closures are unchanged.
    monkeypatch.setattr(bot,'_OFFICIAL_EXCHANGE_CALENDAR_YEARS',frozenset({start.year}))
    monkeypatch.setattr(bot,'_EXCHANGE_CLOSURES',bot._EXCHANGE_CLOSURES|{closure})
    method=getattr(bot,'_roll_'+direction+'_exchange_day')
    with pytest.raises(RuntimeError,match=unknown):
        method(start,require_official=True)


def test_persistent_entry_checks_actual_im_target_before_other_schema_errors():
    signal=base()
    signal.update(core_put_current_contract='MO2612-P-7500',
                  core_put_target_contract='MO2703-P-7500',
                  core_put_target_qty_normalized=1.)
    with pytest.raises(RuntimeError,match='2027'):
        state.validate_v14_new_signal(signal,'IM')


def test_unadopted_research_expiry_does_not_replace_current_target():
    signal=base()
    signal.update(put_sizing_target_expiry_date=date(2027,1,21),
                  core_put_current_contract='MO2612-P-7500',
                  core_put_target_contract='MO2612-P-7500',
                  core_put_target_qty_normalized=1.)
    # Three calendar months is a selection reference, not the adopted expiry.
    bot.require_signal_calendar('IM',signal)


@pytest.mark.parametrize('contract,unknown',[
    ('MO2612-P-7500',False),('MO2703-P-7500',True),
])
def test_prepared_same_contract_positive_target_is_checked(contract,unknown):
    signal=base()
    signal.update(core_put_current_contract=contract,core_put_target_contract=contract,
                  core_put_target_qty_normalized=1.)
    if unknown:
        with pytest.raises(RuntimeError,match='2027'):
            bot.require_signal_calendar('IM',signal)
    else:
        bot.require_signal_calendar('IM',signal)


@pytest.mark.parametrize('month,unknown',[('2612',False),('2703',True)])
def test_public_im_positive_momentum_held_expiry_is_checked(monkeypatch,month,unknown):
    import test_icim_batch3_old_call_20261021 as legacy
    anchors,quotes=legacy.wire(monkeypatch,held=False)
    contract='MO'+month+'-P-7000'
    bot.LIVE_CONTINUATION_ANCHOR['IM'].update(
        post_momentum_put_contract=contract,verified_momentum_put_qty_normalized=.5)
    quotes.loc[len(quotes)]=dict(instrument=contract,lastprice=100.,bprice=99.,sprice=101.,
                                volume=10.,position=20.)
    quotes.attrs['listed_instruments']=list(quotes.instrument)
    original=bot.live_proxy
    def positive_momentum(*args):
        live=original(*args)
        live.update(momentum_next_weight=.5,momentum_current_weight=.5)
        return live
    monkeypatch.setattr(bot,'live_proxy',positive_momentum)
    if unknown:
        with pytest.raises(RuntimeError,match='2027'):
            bot.build_live_trade_signal('IM',now=legacy.CLOCK,mode='close')
    else:
        signal=bot.build_live_trade_signal('IM',now=legacy.CLOCK,mode='close')
        assert signal['momentum_put_current_contract']==contract
        assert signal['momentum_put_target_contract']==contract
        assert signal['momentum_put_target_qty_normalized']>0
