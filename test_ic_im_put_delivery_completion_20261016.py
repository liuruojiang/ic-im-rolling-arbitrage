"""Independent adversarial transition probes; synthetic records, no live writes."""
from datetime import date, timedelta
import pytest
import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as fixture
from test_ic_im_put_execution_repairs_20261016 import candidate, prices, anchor, signal


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('tamper', ['premium', 'price_day', 'no_price_day'])
def test_ledger_rejects_conflicting_confirmed_short_cost(tmp_path, monkeypatch, product, tamper):
    monkeypatch.setattr(execution, 'EFFECTIVE_DATE', fixture.DAY)
    monkeypatch.setattr(bot.fear_grid, 'EFFECTIVE_SIGNAL_DATE', date(2100,1,1))
    record=fixture._record(); store, record=fixture._persist(tmp_path,record)
    before=state.anchors_from_record(record); other='IM' if product=='IC' else 'IC'
    signals={p:fixture._signal(p,before[p],candidate=candidate(p) if p==product else None,
                 extra=dict(momentum_120=.1,valuation_puts_per_full_core=1,valuation_tier=1))
             for p in (product,other)}
    for s in signals.values():s['v14_opening_events']=[]
    record=store.append_confirmed_signals(record,signals)
    before=state.anchors_from_record(record)
    pending=before[product]['v14_short_put_open_pending']; day=execution.as_day(pending['execution_day'])
    event=dict(kind='short_put',plan=pending,status='confirmed_open_research_price',prices=prices(pending,day))
    held=execution.project_short_open(product,before[product],event,day)
    signals={product:fixture._signal(product,held,day),other:fixture._signal(other,before[other],day)}
    signals[product]['v14_opening_events']=[event];signals[other]['v14_opening_events']=[]
    if tamper=='premium': signals[product]['v14_short_put_entry_premium']=999.
    elif tamper=='price_day': signals[product]['v14_short_put_entry_price_day']=date(1999,1,1)
    else: signals[product]['v14_short_put_entry_price_day']=None
    # Required invariant: the independently priced projection owns this cost.
    with pytest.raises(RuntimeError):store.append_confirmed_signals(record,signals)


@pytest.mark.parametrize('product', ['IC','IM'])
def test_short_entry_closes_future_held_after_same_day_quarter_roll(monkeypatch,product):
    monkeypatch.setattr(bot.fear_grid,'EFFECTIVE_SIGNAL_DATE',date(2100,1,1))
    # The daily close's quarterly roll completes before next session's short entry.
    day=date(2026,12,15 if product=='IC' else 17)
    previous=day-timedelta(days=1)
    record=fixture._record()
    record.update(verified_day=previous.isoformat())
    for a in record['products'].values():a['last_verified_day']=previous.isoformat()
    if product=='IM':
        record['products']['IC']['post_core_contract']='IC2703'
        original_roll=bot.quarter_roll.roll_state
        def with_next_listed(p,held,listed,*args):
            # Fixture hardcodes only Dec/Mar; real post-roll IC needs next June listed.
            if p=='IC' and held=='IC2703':listed=[*listed,'IC2706']
            return original_roll(p,held,listed,*args)
        monkeypatch.setattr(bot.quarter_roll,'roll_state',with_next_listed)
    record['digest']=state._digest(record)
    before=state.anchors_from_record(record);a=before[product]
    next_day=bot._roll_forward_exchange_day(day+timedelta(days=1))
    quarter=bot.quarter_roll.roll_state(product,product+'2612',[product+'2612',product+'2703'],
                 day,True,lambda c:bot._third_friday(*bot._contract_month(c)),bot._is_exchange_trading_day)
    assert quarter['roll_confirmed']
    replacement=candidate(product)
    replacement.update(contract='510500P2701M06000' if product=='IC' else 'MO2701-P-6200',
                       expiry=date(2027,1,27 if product=='IC' else 15))
    other='IM' if product=='IC' else 'IC'
    signals={product:fixture._signal(product,a,day,candidate=replacement,
                    extra=dict(momentum_120=.1,valuation_tier=1,valuation_puts_per_full_core=1,
                               **({'put_reference_future':quarter['core_eod_contract']} if product=='IM' else {}))),
             other:fixture._signal(other,before[other],day)}
    for s in signals.values():s['v14_opening_events']=[]
    after=state.derive_next_anchors(record,signals)[product]
    plan=after['v14_short_put_open_pending']
    required_future=next(r['contract'] for r in plan['required_quotes'] if r['kind']=='future')
    assert after['post_core_contract']==quarter['core_eod_contract']
    assert required_future==after['post_core_contract']


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('omit',['revision','event_list','pending_short_event'])
def test_missing_revision_or_required_opening_event_is_rejected(tmp_path,monkeypatch,product,omit):
    monkeypatch.setattr(execution,'EFFECTIVE_DATE',fixture.DAY)
    monkeypatch.setattr(bot.fear_grid,'EFFECTIVE_SIGNAL_DATE',date(2100,1,1))
    record=fixture._record();store,record=fixture._persist(tmp_path,record)
    before=state.anchors_from_record(record);other='IM' if product=='IC' else 'IC'
    signals={p:fixture._signal(p,before[p],candidate=candidate(p) if p==product else None,
              extra=dict(momentum_120=.1,valuation_tier=1,valuation_puts_per_full_core=1)) for p in (product,other)}
    for s in signals.values():s['v14_opening_events']=[]
    if omit=='revision':signals[product].pop('v14_execution_revision')
    elif omit=='event_list':signals[product].pop('v14_opening_events')
    else:
        record=store.append_confirmed_signals(record,signals);before=state.anchors_from_record(record)
        pending=before[product]['v14_short_put_open_pending'];day=execution.as_day(pending['execution_day'])
        event=dict(kind='short_put',plan=pending,status='confirmed_open_research_price',prices=prices(pending,day))
        held=execution.project_short_open(product,before[product],event,day)
        signals={product:fixture._signal(product,held,day),other:fixture._signal(other,before[other],day)}
        for s in signals.values():s['v14_opening_events']=[]
    with pytest.raises(RuntimeError):store.append_confirmed_signals(record,signals)


def _entry_case(tmp_path,monkeypatch,product):
    monkeypatch.setattr(execution,'EFFECTIVE_DATE',fixture.DAY)
    monkeypatch.setattr(bot.fear_grid,'EFFECTIVE_SIGNAL_DATE',date(2100,1,1))
    record=fixture._record();store,record=fixture._persist(tmp_path,record)
    before=state.anchors_from_record(record);other='IM' if product=='IC' else 'IC'
    signals={p:fixture._signal(p,before[p],candidate=candidate(p) if p==product else None,
              extra=dict(momentum_120=.1,valuation_tier=1,valuation_puts_per_full_core=1)) for p in (product,other)}
    for s in signals.values():s['v14_opening_events']=[]
    return store,record,signals,other


@pytest.mark.parametrize('product',['IC','IM'])
def test_pending_plan_cannot_self_certify_another_quarter_future(tmp_path,monkeypatch,product):
    store,record,signals,_=_entry_case(tmp_path,monkeypatch,product)
    pending=signals[product]['v14_short_put_open_pending']
    pending['core_eod_contract']=product+'2703'
    for row in pending['required_quotes']:
        if row['kind']=='future':row['contract']=product+'2703'
    # On a stripped state this plan is internally consistent. Both the full
    # delivery validator and ledger binding must compare the actual close.
    policy.validate_extension(product,{k:v for k,v in signals[product].items() if k!='core_eod_contract'})
    with pytest.raises(RuntimeError,match='绑定当日'):
        state.validate_short_cost_transition(product,state.anchors_from_record(record)[product],signals[product])
    with pytest.raises(RuntimeError,match='known close future'):
        store.append_confirmed_signals(record,signals)


@pytest.mark.parametrize('product',['IC','IM'])
def test_short_cost_cannot_be_confirmed_without_committed_plan(tmp_path,monkeypatch,product):
    store,record,signals,_=_entry_case(tmp_path,monkeypatch,product)
    signals[product].update(v14_short_put_open_pending=None,v14_short_put_entry_premium=999.,
        v14_short_put_entry_price_day=fixture.DAY,v14_short_put_cost_basis='confirmed_dated_open')
    with pytest.raises(RuntimeError,match='独立核验'):
        store.append_confirmed_signals(record,signals)


@pytest.mark.parametrize('product',['IC','IM'])
def test_confirmed_short_can_schedule_and_confirm_another_roll(tmp_path,monkeypatch,product):
    store,record,signals,other=_entry_case(tmp_path,monkeypatch,product)
    record=store.append_confirmed_signals(record,signals)
    before=state.anchors_from_record(record);pending=before[product]['v14_short_put_open_pending']
    day=execution.as_day(pending['execution_day'])
    event=dict(kind='short_put',plan=pending,status='confirmed_open_research_price',prices=prices(pending,day))
    held=execution.project_short_open(product,before[product],event,day)
    replacement=candidate(product,'12');replacement['expiry']=date(2026,12,23 if product=='IC' else 18)
    signals={product:fixture._signal(product,held,day,candidate=replacement,
        extra=dict(momentum_120=.1,valuation_tier=1,valuation_puts_per_full_core=1,v14_short_put_mark=40.)),
        other:fixture._signal(other,before[other],day)}
    signals[product]['v14_opening_events']=[event];signals[other]['v14_opening_events']=[]
    assert signals[product]['v14_action']=='ROLL_SHORT_PUT'
    record=store.append_confirmed_signals(record,signals)
    before=state.anchors_from_record(record);pending=before[product]['v14_short_put_open_pending']
    assert before[product]['v14_short_put_entry_premium'] is None
    day=execution.as_day(pending['execution_day'])
    event=dict(kind='short_put',plan=pending,status='confirmed_open_research_price',prices=prices(pending,day,new_price=80.))
    held=execution.project_short_open(product,before[product],event,day)
    signals={product:fixture._signal(product,held,day),other:fixture._signal(other,before[other],day)}
    signals[product]['v14_opening_events']=[event];signals[other]['v14_opening_events']=[]
    record=store.append_confirmed_signals(record,signals)
    after=state.anchors_from_record(store.load_latest())[product]
    assert after['v14_short_put_entry_premium']==80.
    assert after['v14_short_put_entry_price_day']==day


@pytest.mark.parametrize('product',['IC','IM'])
@pytest.mark.parametrize('outcome',['expired_worthless','assigned'])
def test_verified_short_expiry_can_clear_cost_without_new_opening(product,outcome):
    a=anchor(product)
    a.update(v14_route_state='short_put',v14_cycle_id='verified-test-cycle',
        v14_short_put_contract='510500P2611M06000' if product=='IC' else 'MO2611-P-6200',
        v14_short_put_security_id='10013000' if product=='IC' else None,
        v14_short_put_qty_normalized=5. if product=='IC' else 1.5,
        v14_short_put_expiry=date(2026,11,25 if product=='IC' else 20),
        v14_short_put_entry_premium=120.,v14_short_put_entry_price_day=date(2026,10,19),
        v14_short_put_cost_basis='confirmed_dated_open')
    day=a['v14_short_put_expiry']
    s=signal(product,day,v14_model_settlement_confirmed=True,v14_model_settlement_outcome=outcome,
             v14_recovery_net_pnl_mark=-10.)
    s['next_trade_date']=bot._roll_forward_exchange_day(day+timedelta(days=1))
    out=policy.apply_policy(product,s,a,candidate={'tradable':False})
    assert out['v14_action']==('SHORT_PUT_EXPIRE_WORTHLESS' if outcome=='expired_worthless' else 'SHORT_PUT_ASSIGNED')
    state.validate_short_cost_transition(product,a,out)
    assert out['v14_short_put_entry_premium'] is None


@pytest.mark.parametrize('product',['IC','IM'])
def test_held_short_cost_cannot_be_cleared_by_unrelated_route_signal(product):
    held=anchor(product)
    held.update(v14_route_state='short_put',v14_short_put_expiry=date(2026,11,20),
                v14_short_put_entry_premium=120.,v14_short_put_entry_price_day=date(2026,10,16),
                v14_short_put_cost_basis='confirmed_dated_open')
    fake={**signal(product),**policy.default_extension(product),'v14_action':'HOLD'}
    with pytest.raises(RuntimeError,match='到期分支'):
        state.validate_short_cost_transition(product,held,fake)
