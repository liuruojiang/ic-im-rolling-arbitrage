from copy import deepcopy
from datetime import date,datetime
import json
import gzip
import hashlib
import subprocess
import sys
from pathlib import Path
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as legacy

DAY=date(2026,11,6)
CLOCK=datetime(2026,11,6,15,10,tzinfo=bot.BEIJING)
INTRADAY_CLOCK=datetime(2026,11,6,14,40,tzinfo=bot.BEIJING)

def wire_new_momentum(monkeypatch, *, unknown=True, intraday=False):
    monkeypatch.setattr(legacy,'DAY',DAY)
    monkeypatch.setattr(legacy,'PREVIOUS',date(2026,11,5))
    anchors,mo=legacy.wire(monkeypatch,held=False)
    fear=bot.fear_grid.fetch_score
    def same_day_evidence(*args):
        out=fear(*args)
        out['fear_retrieved_at']='2026-11-06T15:05:00+08:00'
        if intraday:
            out.update(fear_data_status='intraday_provisional',
                       fear_retrieved_at='2026-11-06T14:35:00+08:00',
                       fear_page_updated_at='2026-11-06T14:00:00+08:00')
        return out
    monkeypatch.setattr(bot.fear_grid,'fetch_score',same_day_evidence)
    original=bot.live_proxy
    def positive(*args):
        out=original(*args)
        out.update(momentum_current_weight=0.,momentum_next_weight=.5,momentum_score=3.,
                   momentum_abs_days=20,momentum_abs_on=False,momentum_abs_debounce_active=True)
        return out
    monkeypatch.setattr(bot,'live_proxy',positive)
    for frame in (mo,bot.fetch_cffex_quotes('IM')):
        quote_time='14:35:00' if intraday else '15:05:00'
        frame['source_date']=DAY
        frame['source_time']=quote_time
        frame.attrs.update(source_snapshot_at=DAY.isoformat()+'T'+quote_time+'+08:00',
                           source_snapshot_kind='latest_available_contract_update')
    if unknown:
        mo.loc[len(mo)]=dict(instrument='MO2701-P-8200',lastprice=200.,bprice=199.,sprice=201.,volume=10.,position=20.)
        mo.attrs['listed_instruments']=list(mo.instrument)
    return anchors,mo

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No live requests in candidate test')))

def test_new_im_momentum_calendar_partial_keeps_verified_futures(monkeypatch):
    anchors,_=wire_new_momentum(monkeypatch)
    policy=bot._apply_v14_live_policy
    calls=[]
    def real_policy(*a,**k):
        calls.append(True)
        return policy(*a,**k)
    monkeypatch.setattr(bot,'_apply_v14_live_policy',real_policy)
    monkeypatch.setattr(bot,'_v14_schedule_ordinary_put',lambda *a,**k: pytest.fail('must not create unconfirmed Put plan'))
    out=bot.build_live_trade_signal('IM',CLOCK,'close')
    assert out.get('calendar_partial_revision')=='ic_im_new_put_calendar_partial_20261106_v1',out
    assert calls==[True] and out['v14_route_state']=='future'
    assert out['momentum_units_current']==0 and out['momentum_units_target']==.25
    assert out['core_units_target']==.5 and out['futures_target_confirmed'] is True
    assert out['momentum_put_required_qty_normalized']==.75
    assert out['momentum_put_target_qty_normalized'] is None
    assert out['momentum_put_target_contract'] is None and out['total_put_target_qty_normalized'] is None
    assert out['core_put_target_contract']==out['core_put_current_contract']=='MO2612-P-7200'
    assert out['calendar_provisional'] is True and out['signal_complete'] is False and out['persistence_eligible'] is False
    assert out['unconfirmed_put_legs']==['momentum'] and not out.get('diagnostic_only')
    assert out.get('v14_ordinary_put_pending') is None
    assert 'MO2701' not in str(out.get('put_pricing_evidence'))
    assert 'MO2701' not in out['put_market'] and 'MO2701' not in out['put_target']
    assert bot.LIVE_CONTINUATION_ANCHOR==anchors

def test_known_new_momentum_expiry_keeps_original_complete_path(monkeypatch):
    anchors,_=wire_new_momentum(monkeypatch,unknown=False)
    out=bot.build_live_trade_signal('IM',CLOCK,'close')
    assert not out.get('diagnostic_only'),out
    assert out['calendar_provisional'] is False
    assert out.get('calendar_partial_revision') is None
    assert out['momentum_put_target_qty_normalized']==.75
    assert out['momentum_put_target_contract']=='MO2612-P-7200'
    assert out['signal_complete'] is True and out['persistence_eligible'] is True
    assert bot.LIVE_CONTINUATION_ANCHOR==anchors


@pytest.mark.parametrize('case',['core_from_zero','held_unknown_momentum','policy_failure','other_calendar_failure'])
def test_other_failures_are_not_promoted_to_calendar_partial(monkeypatch,case):
    anchors,_=wire_new_momentum(monkeypatch)
    if case=='core_from_zero':
        bot.LIVE_CONTINUATION_ANCHOR['IM']['verified_core_put_qty_normalized']=0.
    elif case=='held_unknown_momentum':
        bot.LIVE_CONTINUATION_ANCHOR['IM'].update(post_momentum_put_contract='MO2701-P-8200',
                                                verified_momentum_put_qty_normalized=.5)
    else:
        policy=bot._apply_v14_live_policy
        def failed(*a,**k):
            result=policy(*a,**k)
            if case=='policy_failure':
                result.update(v14_action='HOLD_FAIL_CLOSED',v14_action_reason='injected unresolved evidence')
            else:
                bot._SIGNAL_CALENDAR_DIAGNOSTICS.get()['failures'].append(
                    {'date':'2027-01-04','purpose':'next exchange session','officially_verified':False})
            return result
        monkeypatch.setattr(bot,'_apply_v14_live_policy',failed)
    out=bot.build_live_trade_signal('IM',CLOCK,'close')
    assert out.get('calendar_partial_revision') is None
    assert out.get('diagnostic_only') is True
    assert 'momentum_units_target' not in out


def test_normal_input_matches_frozen_original_strategy(tmp_path):
    root=Path(__file__).resolve().parent
    original=tmp_path/'strategy_original.py'
    fixture=root/'tests/fixtures/icim_intraday_observation_20261106'
    provenance=json.loads((fixture/'provenance.json').read_text(encoding='utf-8'))
    packed=(fixture/'strategy_before_20261106.py.gz').read_bytes()
    assert hashlib.sha256(packed).hexdigest()==provenance['gzip_sha256']
    raw=gzip.decompress(packed)
    assert provenance['commit']=='0ac6c7090c2aff46f5956c726cb52c59ba2d8e48'
    assert provenance['path']=='poe_ic_im_mainline_v1_4_bot.py'
    assert hashlib.sha256(raw).hexdigest()==provenance['uncompressed_sha256']
    original.write_bytes(raw)
    script=r'''
import importlib.util,json,sys
from pathlib import Path
if sys.argv[1]!='current':
    spec=importlib.util.spec_from_file_location('poe_ic_im_mainline_v1_4_bot',sys.argv[1])
    mod=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=mod
    spec.loader.exec_module(mod)
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_new_put_calendar_partial_20261106 import wire_new_momentum,CLOCK
mp=pytest.MonkeyPatch()
wire_new_momentum(mp,unknown=False)
mp.setattr(bot.requests,'get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No live requests')))
signal=bot.build_live_trade_signal('IM',CLOCK,'close')
import poe_ic_im_v1_4_state as state
Path(sys.argv[2]).write_text(json.dumps(state._jsonable(signal),ensure_ascii=False,sort_keys=True),encoding='utf-8')
'''
    # Both processes use identical synthetic restored quotes and real policy;
    # only the two real calculation clocks may differ between executions.
    values=[]
    for source,label in ((str(original),'original'),('current','current')):
        dest=tmp_path/(label+'.json')
        child=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,source,str(dest)],
                             cwd=root,capture_output=True,text=True,timeout=30)
        assert child.returncode==0,child.stderr
        result=json.loads(dest.read_text(encoding='utf-8'))
        for key in ('signal_generated_at','signal_calculation_started_at'):
            result.pop(key,None)
        result.get('iv_warning',{}).pop('validation_clock',None)
        values.append(result)
    diff={k:(v,values[1].get(k)) for k,v in values[0].items() if values[1].get(k)!=v}
    if diff:
        print(json.dumps(diff,ensure_ascii=False))
    assert values[0]==values[1]


def test_intraday_volume_placeholder_keeps_verified_futures_with_put_requirement(monkeypatch):
    wire_new_momentum(monkeypatch,intraday=True)
    original=bot.live_proxy
    def placeholder(*a):
        result=original(*a)
        result['momentum_volume_placeholder']=True
        return result
    monkeypatch.setattr(bot,'live_proxy',placeholder)
    out=bot.build_live_trade_signal('IM',INTRADAY_CLOCK,'intraday')
    assert out.get('calendar_partial_revision')=='ic_im_new_put_calendar_partial_20261106_v1',out
    assert out['mode']=='intraday' and out['close_confirmed'] is False
    assert out['momentum_volume_placeholder'] is True
    assert out['momentum_units_target']==.25 and out['momentum_put_required_qty_normalized']==.75
