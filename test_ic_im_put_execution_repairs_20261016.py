from copy import deepcopy
from datetime import date
import json

import pytest

import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot

T, U = date(2026, 10, 16), date(2026, 10, 19)


def anchor(product):
    a = policy.default_extension(product)
    a.update(post_core_contract=product+'2612', last_verified_day=T,
             verified_momentum_put_qty=0, verified_momentum_put_qty_normalized=0.,
             verified_momentum_put_delta=0., verified_core_put_delta=.25,
             verified_total_put_delta=.25, verified_core_put_driver='mom120',
             verified_momentum_put_driver='valuation')
    if product == 'IC':
        a.update(v14_core_put_contract='510500P2612M07500', v14_core_put_security_id='10012000',
                 v14_core_put_qty=5, verified_core_put_qty=5, post_put_contract='510500P2612M07500',
                 post_put_security_id='10012000', post_put_qty=5, verified_put_qty_normalized=5)
    else:
        a.update(post_core_put_contract='MO2612-P-7600',post_put_contract='MO2612-P-7600',
                 verified_core_put_qty_normalized=1.5,verified_put_qty_normalized=1.5,
                 verified_total_put_qty_normalized=1.5,verified_parent_puts=3)
    return a


def signal(product, day=T, **kw):
    s=dict(product=product, market_date=day,next_trade_date=U if day==T else date(2026,10,20),
           close_confirmed=True,valuation_tier=1,valuation_puts_per_full_core=1,
           momentum_120=.1,momentum_next_weight=0.,option_monthly_reset_due=False,
           core_current=product+'2612',core_units_current=.5,core_units_target=.5,
           total_units_current=.5,total_units_target=.5,put_target_momentum_qty=0,
           momentum_put_target_qty_normalized=0.,call_current_contract=None,
           core_put_target_delta=.25,momentum_put_target_delta=0.,
           core_put_driver='mom120',momentum_put_driver='valuation',
           put_target_core_qty=5,core_put_target_qty_normalized=1.5,
           core_put_target_contract='MO2612-P-7600',v13_parent_puts_per_full_core=3)
    s.update(kw);return s


def candidate(product, month='11', premium=100.):
    return dict(tradable=True,contract=f'510500P26{month}M06000' if product=='IC' else f'MO26{month}-P-6200',
                security_id='10013000' if product=='IC' else None,
                expiry=date(2026,int(month),25 if product=='IC' else 20 if month=='11' else 18),
                premium=premium,iv=.4,qty_normalized=5. if product=='IC' else 1.5)


def prices(plan, day=U, new_price=120.):
    return [dict(r,price_day=day,source='saved_real_exchange_open',
                 openprice=new_price if r['contract']==plan['new_contract'] else 50.)
            for r in plan['required_quotes']]


@pytest.mark.parametrize('product',['IC','IM'])
def test_short_signal_quote_is_never_recorded_as_entry_cost(product):
    a=anchor(product); out=policy.apply_policy(product,signal(product),a,candidate=candidate(product))
    assert out['v14_action']=='ENTER_SHORT_PUT'
    assert out['v14_short_put_entry_premium'] is None
    plan=out['v14_short_put_open_pending']
    assert plan['signal_premium']==100.
    event=dict(kind='short_put',status='confirmed_open_research_price',plan=plan,prices=prices(plan))
    projected=execution.project_short_open(product,out,event,U)
    assert projected['v14_short_put_entry_premium']==120.
    assert projected['v14_short_put_cost_basis']=='confirmed_dated_open'
    policy.validate_extension(product,projected)
    rolled=policy.apply_policy(product,signal(product,U,v14_short_put_mark=45.),projected,
                               candidate=candidate(product,'12'),roll_candidate=candidate(product,'12'))
    assert rolled['v14_action']=='ROLL_SHORT_PUT'
    assert rolled['v14_short_put_entry_premium'] is None
    assert len(rolled['v14_short_put_open_pending']['required_quotes'])==2


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('status',['closed_missing_open_price','closed_missed_execution_day'])
def test_failed_short_open_restores_previous_route_and_cost(product,status):
    a=anchor(product); out=policy.apply_policy(product,signal(product),a,candidate=candidate(product))
    plan=out['v14_short_put_open_pending']
    result=execution.project_short_open(product,out,dict(plan=plan,status=status),
                                        U if status=='closed_missing_open_price' else date(2026,10,20))
    assert result['v14_route_state']=='future'
    assert result['v14_short_put_entry_premium'] is None
    assert result['v14_short_put_open_pending'] is None


@pytest.mark.parametrize('bad',['missing_old','wrong_date','nan','duplicate'])
def test_short_roll_evidence_requires_both_exact_dated_positive_legs(bad):
    a=anchor('IM'); out=policy.apply_policy('IM',signal('IM'),a,candidate=candidate('IM'))
    plan=out['v14_short_put_open_pending']; event=dict(plan=plan,status='confirmed_open_research_price',prices=prices(plan))
    if bad=='missing_old':event['prices']=event['prices'][:1]
    if bad=='wrong_date':event['prices'][0]['price_day']=T
    if bad=='nan':event['prices'][0]['openprice']=float('nan')
    if bad=='duplicate':event['prices'].append(deepcopy(event['prices'][0]))
    with pytest.raises(RuntimeError):execution.project_short_open('IM',out,event,U)


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('close_qty',[0.,1.])
def test_committed_profit_open_precedes_new_close_target(product,close_qty):
    a=anchor(product)
    old=a['v14_core_put_contract'] if product=='IC' else a['post_core_put_contract']
    new='510500P2701M06800' if product=='IC' else 'MO2701-P-6800'
    qty=5. if product=='IC' else 1.5
    a.update(v14_core_put_entry_premium=10.,v14_core_put_profit3x_eligible=True,
             v14_profit_pending=True,v14_profit_trigger_day=T,v14_profit_execution_day=U,
             v14_profit_old_contract=old,v14_profit_old_security_id='10012000' if product=='IC' else None,
             v14_profit_old_qty=qty,v14_profit_reentry_contract=new,
             v14_profit_reentry_security_id='10014000' if product=='IC' else None,v14_profit_reentry_qty=qty)
    plan=execution.profit_plan(product,a,U)
    event=dict(kind='profit3x',plan=plan,status='confirmed_open_research_price',prices=prices(plan,new_price=12.))
    held=execution.project_profit_open(product,a,event,U)
    assert held['v14_core_put_entry_premium']==12.
    assert not held['v14_profit_pending']
    kw=dict(core_put_target_delta=0. if close_qty==0 else .1,
            v14_core_put_mark=12.,core_put_target_qty_normalized=close_qty,
            put_target_core_qty=int(close_qty),put_target_contract=new if close_qty else None,
            put_target_security_id='10014000' if product=='IC' and close_qty else None,
            core_put_target_contract=new if close_qty else None,
            v14_core_put_contract=new if close_qty else None,
            v14_core_put_security_id='10014000' if product=='IC' and close_qty else None,
            v14_core_put_qty=int(close_qty))
    out=policy.apply_policy(product,signal(product,U,**kw),held,candidate={'tradable':False})
    bot._v14_schedule_ordinary_put(product,out,held,None)
    assert out['v14_ordinary_put_pending']['legs']['core']['old_contract']==new
    assert out['v14_ordinary_put_pending']['legs']['core']['old_qty']==qty
    assert out['v14_ordinary_put_pending']['legs']['core']['new_qty']==close_qty
    assert next(r for r in event['prices'] if r['contract']==new)['openprice']==12.


def test_adapter_processes_open_without_reading_close_target(monkeypatch):
    a=anchor('IM'); a.update(v14_profit_pending=True,v14_profit_trigger_day=T,v14_profit_execution_day=U,
        v14_profit_old_contract='MO2612-P-7600',v14_profit_old_qty=1.5,
        v14_profit_reentry_contract='MO2701-P-6800',v14_profit_reentry_qty=1.5)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,'IM',a)
    monkeypatch.setattr(bot,'_v14_committed_open_prices',lambda product,plan,day,clock,cache: prices(plan,new_price=12.))
    events=bot._v14_project_committed_opens('IM',U,None,{})
    assert events[0]['status']=='confirmed_open_research_price'
    assert bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_put_contract']=='MO2701-P-6800'
    assert bot.LIVE_CONTINUATION_ANCHOR['IM']['v14_core_put_entry_premium']==12.


def test_legacy_short_cost_is_not_silently_certified():
    a=anchor('IM'); a.update(v14_route_state='short_put',v14_cycle_id='old-cycle',
         v14_short_put_contract='MO2611-P-6200',v14_short_put_qty_normalized=1.5,
         v14_short_put_entry_premium=100.,v14_short_put_expiry=date(2026,11,20))
    out=policy.apply_policy('IM',signal('IM',U,v14_short_put_mark=1.),a,candidate=candidate('IM','12'))
    assert out['v14_action']=='HOLD'
    assert out['v14_action_reason']=='legacy_entry_cost_unverified'


@pytest.mark.parametrize('product',['IC','IM'])
def test_missing_profit_open_cannot_erase_old_core_using_close_zero(product):
    a=anchor(product)
    old=a['v14_core_put_contract'] if product=='IC' else a['post_core_put_contract']
    qty=5. if product=='IC' else 1.5
    a.update(v14_core_put_entry_premium=10.,v14_core_put_profit3x_eligible=True,
        v14_profit_pending=True,v14_profit_trigger_day=T,v14_profit_execution_day=U,
        v14_profit_old_contract=old,v14_profit_old_security_id='10012000' if product=='IC' else None,
        v14_profit_old_qty=qty,v14_profit_reentry_contract='510500P2701M06800' if product=='IC' else 'MO2701-P-6800',
        v14_profit_reentry_security_id='10014000' if product=='IC' else None,v14_profit_reentry_qty=qty)
    s=signal(product,U,put_target_core_qty=0,core_put_target_qty_normalized=0.,
             v14_core_put_contract=None,v14_core_put_security_id=None,v14_core_put_qty=0,
             core_put_target_delta=0.)
    out=policy.apply_policy(product,s,a,candidate={'tradable':False})
    assert out['v14_profit_pending']
    assert out['v14_action']=='WAIT_CORE_PUT_PROFIT3X_REENTER'
    if product=='IC':
        assert out['v14_core_put_contract']==old
        assert out['put_target_core_qty']==qty
    else:
        assert out['core_put_target_contract']==old
        assert out['core_put_target_qty_normalized']==qty


@pytest.mark.parametrize('product',['IC','IM'])
def test_pending_short_cannot_omit_synchronized_old_leg(product):
    out=policy.apply_policy(product,signal(product),anchor(product),candidate=candidate(product))
    out['v14_short_put_open_pending']['required_quotes']=out['v14_short_put_open_pending']['required_quotes'][:1]
    with pytest.raises(RuntimeError,match='synchronized'):
        policy.validate_extension(product,out)


@pytest.mark.parametrize('product',['IC','IM'])
def test_short_missing_open_after_json_roundtrip_keeps_bookkeeping_day(product):
    out=policy.apply_policy(product,signal(product),anchor(product),candidate=candidate(product))
    out['last_verified_day']=T
    raw=json.loads(json.dumps(out,default=lambda x:x.isoformat()))
    event=dict(plan=raw['v14_short_put_open_pending'],status='closed_missing_open_price')
    held=execution.project_short_open(product,raw,event,U)
    assert held['last_verified_day']==T
    assert held['v14_route_state']=='future'
    assert held['v14_short_put_entry_premium'] is None


@pytest.mark.parametrize('product',['IC','IM'])
def test_profit_open_and_later_zero_target_survive_real_hash_ledger_restart(tmp_path,monkeypatch,product):
    import test_ic_im_ordinary_put_route_fix_20261013 as fixture
    import poe_ic_im_v1_4_state as state
    monkeypatch.setattr(execution,'EFFECTIVE_DATE',fixture.DAY)
    record=fixture._record(); a=record['products'][product]
    qty=float(a['v14_core_put_qty'] if product=='IC' else a['verified_core_put_qty_normalized'])
    old=a['v14_core_put_contract'] if product=='IC' else a['post_core_put_contract']
    new='510500P2701M06800' if product=='IC' else 'MO2701-P-6800'
    a.update(v14_profit_pending=True,v14_profit_trigger_day=fixture.PREVIOUS.isoformat(),
             v14_profit_execution_day=fixture.DAY.isoformat(),v14_profit_old_contract=old,
             v14_profit_old_security_id=a.get('v14_core_put_security_id') if product=='IC' else None,
             v14_profit_old_qty=qty,v14_profit_reentry_contract=new,
             v14_profit_reentry_security_id='10014000' if product=='IC' else None,v14_profit_reentry_qty=qty)
    store,record=fixture._persist(tmp_path,record); before=state.anchors_from_record(record)
    plan=execution.profit_plan(product,before[product],fixture.DAY)
    event=dict(kind='profit3x',plan=plan,status='confirmed_open_research_price',
               prices=prices(plan,fixture.DAY,new_price=12.))
    held=execution.project_profit_open(product,before[product],event,fixture.DAY)
    if product=='IC':
        extra=dict(core_put_target_delta=0.,total_put_target_delta=0.,put_target_core_qty=0,
                   put_target_total_qty=0,v14_core_put_qty=0,v14_core_put_contract=None,v14_core_put_security_id=None)
    else:
        extra=dict(core_put_target_qty_normalized=0.,core_put_target_contract=None,
                   put_target_contract=None,total_put_target_qty_normalized=0.,v13_parent_puts_per_full_core=0)
    signals={product:fixture._signal(product,held,extra=extra)}
    signals[product]['v14_opening_events']=[event]
    other='IM' if product=='IC' else 'IC'
    signals[other]=fixture._signal(other,before[other]);signals[other]['v14_opening_events']=[]
    result=store.append_confirmed_signals(record,signals)
    reopened=state.StateStore(store.root).load_latest()
    assert reopened['digest']==result['digest']
    after=reopened['products'][product]
    assert after['v14_core_put_entry_premium']==12.
    assert after['v14_ordinary_put_pending']['legs']['core']['old_contract']==new
    assert after['v14_ordinary_put_pending']['legs']['core']['new_qty']==0.


@pytest.mark.parametrize('product',['IC','IM'])
def test_short_open_cost_survives_real_hash_ledger_restart(tmp_path,monkeypatch,product):
    import test_ic_im_ordinary_put_route_fix_20261013 as fixture
    import poe_ic_im_v1_4_state as state
    monkeypatch.setattr(execution,'EFFECTIVE_DATE',fixture.DAY)
    monkeypatch.setattr(bot.fear_grid,'EFFECTIVE_SIGNAL_DATE',date(2100,1,1))
    record=fixture._record();store,record=fixture._persist(tmp_path,record)
    before=state.anchors_from_record(record);other='IM' if product=='IC' else 'IC'
    signals={p:fixture._signal(p,before[p],candidate=candidate(p) if p==product else None,
                              extra=dict(momentum_120=.1,valuation_puts_per_full_core=1,valuation_tier=1))
             for p in (product,other)}
    for s in signals.values():s['v14_opening_events']=[]
    result=store.append_confirmed_signals(record,signals)
    reopened=state.StateStore(store.root).load_latest();after=state.anchors_from_record(reopened)
    assert after[product]['v14_short_put_entry_premium'] is None
    pending=after[product]['v14_short_put_open_pending'];day=execution.as_day(pending['execution_day'])
    event=dict(kind='short_put',plan=pending,status='confirmed_open_research_price',prices=prices(pending,day))
    held=execution.project_short_open(product,after[product],event,day)
    signals={product:fixture._signal(product,held,day),other:fixture._signal(other,after[other],day)}
    signals[product]['v14_opening_events']=[event];signals[other]['v14_opening_events']=[]
    result=store.append_confirmed_signals(reopened,signals)
    final=state.StateStore(store.root).load_latest()
    assert final['digest']==result['digest']
    assert final['products'][product]['v14_short_put_entry_premium']==120.
    assert final['products'][product]['v14_short_put_cost_basis']=='confirmed_dated_open'
