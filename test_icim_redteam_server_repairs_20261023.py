"""Repair regressions: isolated state, original real ledger fixture, synthetic faults."""
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

import os
OUT=Path(os.environ["ICIM_TEST_OUTPUT_DIR"])
FIXTURE = Path(__file__).parent / "tests/fixtures/icim_verified_ledger_20261021"

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


@pytest.mark.parametrize("clock,query", [("2026-10-22T08:00:00+08:00","当前持仓"),("2026-10-22T09:10:00+08:00","当前持仓"),("2026-10-22T09:30:00+08:00","当前持仓"),("2026-10-22T14:30:00+08:00","当前持仓"),("2026-10-22T12:30:00+08:00","当前持仓"),("2026-10-24T10:30:00+08:00","信号"),("2026-10-21T18:00:00+08:00","信号")])
def test_readonly_snapshot_uses_latest_verified_record(monkeypatch,tmp_path,clock,query):
    import shutil
    import poe_ic_im_v1_4_server as server
    import poe_ic_im_v1_4_state as state
    source=FIXTURE
    copied=tmp_path/"ledger_copy"
    shutil.copytree(source,copied,ignore=shutil.ignore_patterns(".ledger.lock"))
    monkeypatch.setenv("ICIM_EVIDENCE_DIR",str(tmp_path/"empty_evidence"))
    monkeypatch.setattr(bot,"LIVE_CONTINUATION_ANCHOR",deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    coordinator=server.LedgerCoordinator(state.StateStore(copied))
    latest=coordinator.store.load_latest()
    before=latest["digest"]
    assert latest["verified_day"]=="2026-10-21"
    if query=="信号":
        def unavailable(*_a,**_k): raise RuntimeError("controlled source unavailable")
        monkeypatch.setattr(bot,"build_live_signal_with_transport_retry",unavailable)
    report,attachments,observed=coordinator.execute_query(query,datetime.fromisoformat(clock),persist_confirmed=False)
    artifact=OUT/("snapshot_"+clock.replace(":","").replace("+", "_")+".md")
    artifact.write_text(report,encoding="utf-8")
    assert observed=={} and not attachments
    assert coordinator.store.load_latest()["digest"]==before
    assert report.count("最后逐腿核验快照（2026-10-21") == 2
    assert "最后逐腿核验快照（2026-10-20" not in report


@pytest.mark.parametrize("failure", [None, RuntimeError, __import__("asyncio").CancelledError])
def test_capture_scopes_own_head_and_restores_global_even_on_cancel(monkeypatch, tmp_path, failure):
    import asyncio
    module, owner, old, signals, clock = _coordinator(tmp_path, monkeypatch)
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    identity = bot.LIVE_CONTINUATION_ANCHOR
    seen = []
    ledger_before = snapshot(owner.store.root)

    def capture(strategy, root, now):
        seen.append(deepcopy(strategy.LIVE_CONTINUATION_ANCHOR))
        strategy.LIVE_CONTINUATION_ANCHOR["IC"]["capture_local_mutation"] = True
        if failure:
            raise failure("injected capture interruption")
        return {"status": "captured", "day": str(now.date())}

    monkeypatch.setattr(adapter, "capture_daily", capture)
    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            owner.capture_current_inputs(clock)
    else:
        result = owner.capture_current_inputs(clock)
        assert result["status"] == ("incomplete" if failure else "captured")
    assert seen == [state.anchors_from_record(old)]
    assert bot.LIVE_CONTINUATION_ANCHOR is identity
    assert bot.LIVE_CONTINUATION_ANCHOR == before
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None
    assert snapshot(owner.store.root) == ledger_before


def test_two_coordinators_capture_their_own_heads(monkeypatch, tmp_path):
    from test_icim_batch3_concurrency_20261021 import server, store
    module = server(monkeypatch, tmp_path)
    first_store, first_record = store(tmp_path / "first", "first-head")
    second_store, second_record = store(tmp_path / "second", "second-head")
    first, second = module.LedgerCoordinator(first_store), module.LedgerCoordinator(second_store)
    seen = []
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)

    def capture(strategy, root, now):
        seen.append(deepcopy(strategy.LIVE_CONTINUATION_ANCHOR))
        return {"status": "captured", "day": str(now.date())}

    monkeypatch.setattr(adapter, "capture_daily", capture)
    clock = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)
    first.capture_current_inputs(clock)
    second.capture_current_inputs(clock)
    assert seen == [state.anchors_from_record(first_record), state.anchors_from_record(second_record)]
    assert bot.LIVE_CONTINUATION_ANCHOR == original


def test_capture_still_rejects_genuinely_future_snapshot(monkeypatch, tmp_path):
    module, owner, old, signals, clock = _coordinator(tmp_path, monkeypatch)
    ticks = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])

    def capture(strategy, root, now):
        ticks[0] += 2.
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            now + timedelta(seconds=3), strategy._iv_observation_clock(now), now.date(), "future frame")
        return {"status": "captured", "day": str(now.date())}

    monkeypatch.setattr(adapter, "capture_daily", capture)
    result = owner.capture_current_inputs(clock)
    assert result["status"] == "incomplete"
    assert "晚于采集时刻" in result["reason"]
    assert owner._evidence_capture_bucket is None


def test_refresh_capture_clock_is_sampled_after_catch_up(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from test_icim_batch3_concurrency_20261021 import server
    module = server(monkeypatch, tmp_path)
    start = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)
    end = start + timedelta(minutes=11)
    clock = [start]
    seen = []

    def catch_up(now):
        assert now == start
        clock[0] = end

    async def invoke(fn, *args):
        return fn(*args)

    async def stop(_seconds):
        raise asyncio.CancelledError

    owner = SimpleNamespace(catch_up_once=catch_up,
                            capture_current_inputs=lambda: seen.append(bot._now_beijing()), last_refresh_error=None)
    monkeypatch.setattr(module, "coordinator", owner)
    monkeypatch.setattr(bot, "_now_beijing", lambda: clock[0])
    monkeypatch.setattr(module.asyncio, "to_thread", invoke)
    monkeypatch.setattr(module.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._refresh_loop())
    assert seen == [end]


def test_capture_observation_clock_includes_shared_lock_wait(monkeypatch, tmp_path):
    module, owner, old, signals, clock = _coordinator(tmp_path, monkeypatch)
    ticks = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])

    class DelayedLock:
        def __enter__(self):
            ticks[0] += 2.
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(owner, "lock", DelayedLock())

    def capture(strategy, root, now):
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            clock + timedelta(seconds=1), strategy._iv_observation_clock(now), clock.date(), "arrived during lock wait")
        return {"status": "captured", "day": str(now.date())}

    monkeypatch.setattr(adapter, "capture_daily", capture)
    assert owner.capture_current_inputs(clock)["status"] == "captured"
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None


def test_refresh_worker_samples_clock_after_executor_queue_delay(monkeypatch, tmp_path):
    import asyncio
    from test_icim_batch3_concurrency_20261021 import server
    module = server(monkeypatch, tmp_path)
    owner = module.LedgerCoordinator()
    start = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)
    clock = [start]
    seen = []

    def catch_up(now):
        assert now == start
        clock[0] += timedelta(minutes=10)

    def capture(strategy, root, now):
        seen.append(now)
        return {"status": "captured", "day": str(now.date())}

    async def invoke(fn, *args):
        if fn.__name__ == "capture_current_inputs":
            assert args == ()
            clock[0] += timedelta(minutes=3)
        return fn(*args)

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(module, "coordinator", owner)
    monkeypatch.setattr(owner, "catch_up_once", catch_up)
    monkeypatch.setattr(bot, "_now_beijing", lambda: clock[0])
    monkeypatch.setattr(adapter, "capture_daily", capture)
    monkeypatch.setattr(module.asyncio, "to_thread", invoke)
    monkeypatch.setattr(module.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._refresh_loop())
    assert seen == [start + timedelta(minutes=13)]


def test_same_day_recomputation_keeps_predecessor_but_displays_latest(monkeypatch, tmp_path):
    import shutil
    from test_icim_batch3_concurrency_20261021 import server
    module = server(monkeypatch, tmp_path)
    source = FIXTURE
    copied = tmp_path / "ledger_copy"
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns(".ledger.lock"))
    owner = module.LedgerCoordinator(state.StateStore(copied))
    latest = owner.store.load_latest()
    previous = owner.store.load_sequence(latest["sequence"] - 1)
    seen = []

    def body(_self):
        seen.append(deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
        with bot.poe.start_message() as message:
            for product in ("IC", "IM"):
                bot._write_last_verified_snapshot(message, product)

    monkeypatch.setattr(bot.ICIMMainlinesBot, "run", body)
    before = snapshot(owner.store.root)
    text, attachments, observed = owner.execute_query(
        "信号", datetime(2026, 10, 21, 18, tzinfo=bot.BEIJING), persist_confirmed=False)
    assert seen == [state.anchors_from_record(previous)]
    assert text.count("最后逐腿核验快照（2026-10-21") == 2
    assert not attachments and not observed
    assert snapshot(owner.store.root) == before
    assert bot._VERIFIED_DISPLAY_ANCHORS.get() is None


def test_display_context_restored_after_uncaught_bot_error(monkeypatch, tmp_path):
    module, owner, old, signals, clock = _coordinator(tmp_path, monkeypatch)

    def failure(_self):
        assert bot._VERIFIED_DISPLAY_ANCHORS.get() == state.anchors_from_record(old)
        raise RuntimeError("uncaught renderer failure")

    monkeypatch.setattr(bot.ICIMMainlinesBot, "run", failure)
    with pytest.raises(RuntimeError, match="uncaught renderer failure"):
        owner.execute_query("当前持仓", clock)
    assert bot._VERIFIED_DISPLAY_ANCHORS.get() is None


@pytest.mark.parametrize("outer_collection_context", [False, True])
def test_capture_runner_accounts_for_readonly_preflight_elapsed_time(monkeypatch, tmp_path, outer_collection_context):
    from contextlib import nullcontext
    import requests
    import capture_ic_im_v1_4_inputs as runner
    from test_icim_free_sources_capture_runner_20261022 import fixture_runner
    selected_clock = datetime(2026, 10, 22, 18, 45, tzinfo=bot.BEIJING)
    owner, store, record = fixture_runner(tmp_path)
    before = runner.ledger_hashes(store.root)
    tick = [100.]
    seen = {}
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: tick[0])

    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            current = selected_clock + timedelta(seconds=tick[0] - 100.)
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr(runner, "datetime", Wall)
    original_load = runner.load_ledger_readonly

    def delayed_real_readonly_load(*args):
        value = original_load(*args)
        # Actual readonly migration/hash-chain validation still runs.
        tick[0] += 2.
        return value

    def consume_current_frame(strategy, root, clock):
        quote_stamp = selected_clock + timedelta(seconds=1)
        observation = strategy._iv_observation_clock(clock)
        seen.update(start=clock.isoformat(), quote=quote_stamp.isoformat(),
                    wall=Wall.now(bot.BEIJING).isoformat(), observation=observation.isoformat())
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            quote_stamp, observation, clock.date(), "current frame after readonly preflight")
        return {"status": "captured", "day": str(clock.date()), "products": {}}

    monkeypatch.setattr(runner, "load_ledger_readonly", delayed_real_readonly_load)
    monkeypatch.setattr(adapter, "capture_daily", consume_current_frame)
    with bot.collection_clock(selected_clock) if outer_collection_context else nullcontext():
        result = owner.cycle()
    seen["result"] = result
    (OUT / f"collector_preflight_outer_{outer_collection_context}.json").write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    assert runner.ledger_hashes(store.root) == before
    assert result["ledger_paths_and_bytes_unchanged"] is True
    assert result["status"] == "captured", seen
    assert result["started_at"] == selected_clock.isoformat()
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None
