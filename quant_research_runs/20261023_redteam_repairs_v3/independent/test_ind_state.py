from copy import deepcopy
from datetime import datetime, timedelta, date
import json
from pathlib import Path
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import ic_im_evidence_adapter_v1 as adapter
from test_icim_b3adv_state_20261021 import prepare
from test_icim_expert_state_20261021 import _coordinator
from test_icim_round2_audit_lifecycle_20261021 import snapshot

OUT=Path(__import__('os').environ['ICIM_TEST_OUTPUT_DIR'])

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: pytest.fail('unexpected network'))
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.delenv('ICIM_DISABLE_EVIDENCE_CAPTURE',raising=False)


def test_capture_after_successful_public_append_uses_committed_head(monkeypatch,tmp_path):
    module, owner, old, signals, clock=_coordinator(tmp_path,monkeypatch)
    text,_,observed=owner.execute_query('信号',clock)
    latest=owner.store.load_latest()
    assert latest['sequence']==old['sequence']+1
    before=snapshot(owner.store.root)
    seen={}
    def capture(strategy,root,now):
        seen.update(anchors=state._jsonable(strategy.LIVE_CONTINUATION_ANCHOR),
                    observation_clock=str(strategy._iv_observation_clock(now)))
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    owner.capture_current_inputs(clock)
    observation={'expected_day':latest['verified_day'],
                 'actual_day':seen['anchors']['IC']['last_verified_day'],
                 'old_day':old['verified_day'],'formal_store_unchanged':snapshot(owner.store.root)==before}
    (OUT/'stale_capture_anchor_observation.json').write_text(json.dumps(observation,indent=2),encoding='utf-8')
    assert snapshot(owner.store.root)==before
    assert seen['anchors']['IC']['last_verified_day']==latest['verified_day']


def test_server_capture_advances_observation_clock(monkeypatch,tmp_path):
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    owner.evidence_root=tmp_path/'evidence'
    ticks=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:ticks[0])
    def capture(strategy,root,now):
        ticks[0]+=2.
        stamp=now+timedelta(seconds=1)
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            stamp,strategy._iv_observation_clock(now),now.date(),'synthetic current CFFEX frame')
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    result=owner.capture_current_inputs(clock)
    (OUT/'missing_capture_clock_observation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['status']=='captured',result


def test_outer_collection_context_accepts_same_arriving_snapshot(monkeypatch,tmp_path):
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    owner.evidence_root=tmp_path/'evidence'
    ticks=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:ticks[0])
    def capture(strategy,root,now):
        ticks[0]+=2.
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            now+timedelta(seconds=1),strategy._iv_observation_clock(now),now.date(),'control CFFEX frame')
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    with bot.collection_clock(clock):
        result=owner.capture_current_inputs(clock)
    assert result['status']=='captured'


@pytest.mark.parametrize('bad_product',['IC','IM'])
def test_bad_product_rejected_without_any_persistent_write(monkeypatch,tmp_path,bad_product):
    store,old,signals=prepare(tmp_path,monkeypatch)
    before=snapshot(store.root)
    signals[bad_product]['diagnostic_only']=True
    with pytest.raises(RuntimeError):store.append_confirmed_signals(old,signals)
    assert snapshot(store.root)==before


@pytest.mark.parametrize('field,value',[('sequence',999),('verified_day','2026-10-01')])
def test_mutated_current_without_rehash_rejected(monkeypatch,tmp_path,field,value):
    store,old,signals=prepare(tmp_path,monkeypatch)
    before=snapshot(store.root)
    old[field]=value
    with pytest.raises(RuntimeError):store.append_confirmed_signals(old,signals)
    assert snapshot(store.root)==before


def test_partial_journal_write_is_recoverable_after_restart(monkeypatch,tmp_path):
    store,old,signals=prepare(tmp_path,monkeypatch)
    writer=store._atomic_write
    def fail_head(path,record):
        if path==store.latest_path:raise OSError('controlled interruption before head replace')
        return writer(path,record)
    monkeypatch.setattr(store,'_atomic_write',fail_head)
    with pytest.raises(OSError):store.append_confirmed_signals(old,signals)
    restarted=state.StateStore(store.root)
    assert restarted.load_latest()['digest']==old['digest']
    appended=restarted.append_confirmed_signals(old,signals)
    assert appended['sequence']==old['sequence']+1
    assert restarted.load_latest()['digest']==appended['digest']
    assert len(list(store.journal_dir.glob('000001-*.json')))==1
    with pytest.raises(RuntimeError):restarted.append_confirmed_signals(old,signals)


@pytest.mark.parametrize('with_context',[False,True])
def test_real_daily_capture_seals_current_arriving_frame(monkeypatch,tmp_path,with_context):
    import pandas as pd
    import ic_im_signal_evidence_v1 as evidence
    from contextlib import nullcontext
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    owner.evidence_root=tmp_path/'evidence'
    ticks=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:ticks[0])
    class Wall(datetime):
        @classmethod
        def now(cls,tz=None):
            t=clock+timedelta(seconds=ticks[0]-100.)
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    monkeypatch.setattr(adapter,'datetime',Wall)
    monkeypatch.setattr(evidence,'_now',lambda:clock+timedelta(seconds=ticks[0]-100.))
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',state.anchors_from_record(old))
    def role(name,key,fn):return evidence.current_session.get().call(name,key,fn)
    def quotes(product,now):
        def load():
            ticks[0]+=2.
            bot.quote_snapshot_guard.official_snapshot_evidence(
                clock+timedelta(seconds=1),bot._iv_observation_clock(now),now.date(),'synthetic current CFFEX frame')
            frame=pd.DataFrame([{'instrument':product+'2612','last':6500.}])
            frame.attrs['source']='中金所'
            return frame
        return role('cffex.quotes',{'product':product},load)
    monkeypatch.setattr(bot,'fetch_cffex_quotes',quotes)
    monkeypatch.setattr(bot,'fetch_510500_expiries_with_failover',lambda:role('ic.months',{},lambda:['202612']))
    monkeypatch.setattr(bot,'fetch_510500_quote_with_failover',lambda:role('ic.etf',{},lambda:{'last':6.8}))
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',lambda month,day:role('ic.chain',{'month':month,'day':day},lambda:pd.DataFrame([{'contract':'510500P2612M06500'}])))
    monkeypatch.setattr(bot,'live_proxy',lambda product,now:role('market.live',{'product':product},lambda:{}))
    monkeypatch.setattr(bot,'_fear_score_evidence',lambda day,mode:role('fear.score',{'day':day,'mode':mode},lambda:{'fear_greed_index':30.}))
    monkeypatch.setattr(bot,'_fear_reference_evidence',lambda day:role('fear.reference',{'day':day},lambda:{}))
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda product,day:role('rate.resolve',{'product':product},lambda:.02))
    before=snapshot(owner.store.root)
    with bot.collection_clock(clock) if with_context else nullcontext():
        result=owner.capture_current_inputs(clock)
    assert snapshot(owner.store.root)==before
    metadata={}
    for product,data in result['products'].items():
        if data.get('manifest'):
            manifest=json.loads(Path(data['manifest']).read_text('utf-8'))
            metadata[product]={'complete':manifest['body']['complete'],'metadata':evidence.decode(manifest['body']['metadata'])}
    (OUT/f'full_capture_context_{with_context}.json').write_text(json.dumps({'result':result,'sealed':metadata},ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['status']=='captured',result

@pytest.mark.parametrize('outcome',['success','error','cancel'])
def test_capture_uses_own_head_and_restores_foreign_anchor_even_on_cancel(monkeypatch,tmp_path,outcome):
    import asyncio
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    own=state.anchors_from_record(owner.store.load_latest())
    foreign=deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    foreign['IC']['last_verified_day']=date(2020,1,2)
    foreign['IM']['last_verified_day']=date(2020,1,3)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',foreign)
    saved=deepcopy(foreign);before=snapshot(owner.store.root)
    def capture(strategy,root,now):
        assert strategy.LIVE_CONTINUATION_ANCHOR==own
        if outcome=='error':raise RuntimeError('isolated ordinary capture failure')
        if outcome=='cancel':raise asyncio.CancelledError('isolated cancellation')
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    if outcome=='cancel':
        with pytest.raises(asyncio.CancelledError):owner.capture_current_inputs(clock)
    else:
        result=owner.capture_current_inputs(clock)
        assert result['status']==('captured' if outcome=='success' else 'incomplete')
    assert bot.LIVE_CONTINUATION_ANCHOR==saved
    assert snapshot(owner.store.root)==before


def test_server_capture_still_rejects_timestamp_after_actual_observation(monkeypatch,tmp_path):
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    ticks=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:ticks[0])
    def capture(strategy,root,now):
        ticks[0]+=2.
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            now+timedelta(seconds=3),strategy._iv_observation_clock(now),now.date(),'future synthetic CFFEX frame')
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    result=owner.capture_current_inputs(clock)
    assert result['status']=='incomplete'
    assert owner._evidence_capture_bucket is None
