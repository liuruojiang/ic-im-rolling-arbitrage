"""Independent black-box delivery boundaries; real saved 10-22 payload, no SMTP.

Requires an explicit immutable final strategy SHA/root. This file is deliberately
outside automation production tests and must not be run before release pin notice.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[3]
AUTO = Path(os.environ.get('ICIM_AUDIT_AUTOMATION_ROOT', str(ROOT/'quant_research_runs/20261023_daily_release_v3/automation_checkout')))
STRATEGY = Path(os.environ['ICIM_AUDIT_STRATEGY_ROOT'])
PIN = os.environ['ICIM_AUDIT_FINAL_SHA']
sys.path.insert(0, str(AUTO/'scripts'))
import build_ic_im_v1_4_digest as renderer
import verify_icim_source_release as source_check
import check_ic_im_v1_4_delivery as day_gate
import check_ic_im_v1_4_result_day_delivery as result_gate
import prepare_ic_im_v1_4_marker as marker
import resolve_ic_im_v1_4_attempt as attempt
import restore_ic_im_v1_4_ledger as restore

HISTORY = ROOT/'quant_research_runs/20261023_daily_release_sync_v1/local_handoff_preview'

@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('independent delivery audit forbids network')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)

@pytest.fixture
def payload():
    value=json.loads((HISTORY/'report/result.json').read_text('utf-8'))
    assert value['market_date']=='2026-10-22'
    assert value['digest']=='49fb159fbadf894b6c64e8ca886faef49d62c5e8e31f0415690209f834317cc0'
    return value

@pytest.fixture
def proof(): return source_check.verify(STRATEGY, PIN)

def write_proof(tmp_path, value):
    p=tmp_path/'source.json';p.write_text(json.dumps(value),'utf-8');return str(p)

def test_pinned_execution_source_and_consumer_agree(proof,tmp_path):
    accepted=renderer.source_provenance(write_proof(tmp_path,proof), PIN)
    assert accepted['strategy_sha']==PIN and accepted['release']=='20261023-redteam-repairs-v3-r3'

@pytest.mark.parametrize('wrong', ['main','0'*40,'8038980de86251b2c5133da04e788dc2c49c84ff'])
def test_wrong_checkout_identity_cannot_produce_a_verified_proof(wrong):
    assert wrong!=PIN
    with pytest.raises(ValueError): source_check.verify(STRATEGY,wrong)

@pytest.mark.parametrize('mutation', ['wrong_sha','old_release','wrong_release','old_manifest_hash','tampered_source_hash','missing_file','future_verification','naive_verification'])
def test_proof_substitution_cannot_reach_renderer(proof,tmp_path,mutation):
    attacked=copy.deepcopy(proof)
    if mutation=='wrong_sha': attacked['strategy_sha']='0'*40
    elif mutation=='old_release': attacked['release']='20261023-audited-inputs-v1'
    elif mutation=='wrong_release': attacked['release']='20261023-redteam-repairs-v3-r2'
    elif mutation=='old_manifest_hash': attacked['manifest_sha256']='0'*64
    elif mutation=='tampered_source_hash': attacked['source_sha256']['poe_ic_im_mainline_v1_4_bot.py']='0'*64
    elif mutation=='missing_file': attacked['source_sha256'].pop('capture_ic_im_v1_4_inputs.py')
    elif mutation=='future_verification': attacked['verified_at']='2099-01-01T00:00:00+00:00'
    else: attacked['verified_at']='2026-10-23T00:00:00'
    with pytest.raises(ValueError): renderer.source_provenance(write_proof(tmp_path,attacked),PIN)

def test_real_old_proof_cannot_be_relabelled_as_new_release(tmp_path):
    old=json.loads((HISTORY/'source-proof.json').read_text('utf-8'))
    old['strategy_sha']=PIN
    old['release']='20261023-redteam-repairs-v3-r3'
    with pytest.raises(ValueError):renderer.source_provenance(write_proof(tmp_path,old),PIN)

@pytest.mark.parametrize('pollution', [
    {'market_date':'2026-10-23','verified_day':'2026-10-23','product':'FORGED_PRODUCT'},
    {'total_units_target':98765,'grid_target':98765,'put_target_total_qty':98765,'v14_action':'FORGED_ACTION'},
    {'fear_source_status':'verified','fear_data_date':'2026-10-23','fear_source_warning':'FORGED_WARNING'},
    {'put_target':'FORGED_PUT','put_target_contract':'FORGED_PUT','call_target_contract':'FORGED_CALL'},
])
def test_real_payload_reference_pollution_preserves_both_renderings(payload,pollution):
    expected=(renderer.build_success(payload,'',''),renderer.build_success_html(payload,''))
    attacked=copy.deepcopy(payload);attacked.setdefault('jiu_reference',{}).update(pollution)
    original=copy.deepcopy(attacked)
    assert (renderer.build_success(attacked,'',''),renderer.build_success_html(attacked,''))==expected
    assert attacked==original

def test_old_signal_render_keeps_actual_market_day(payload):
    subject,body,_=renderer.build_success(payload,'','')
    assert '2026-10-22' in subject and '2026-10-23' not in subject
    assert payload['market_date']=='2026-10-22'

@pytest.mark.parametrize('mutation',['top_market_day','one_leg_day','both_leg_days','verified_day','completed_day'])
def test_real_old_signal_cannot_be_partially_renamed_today(payload,mutation):
    attacked=copy.deepcopy(payload)
    if mutation=='top_market_day':attacked['market_date']='2026-10-23'
    elif mutation=='one_leg_day':attacked['signals']['IC']['market_date']='2026-10-23'
    elif mutation=='both_leg_days':
        for s in attacked['signals'].values():s['market_date']='2026-10-23'
    else:attacked[mutation]='2026-10-23'
    with pytest.raises((ValueError,RuntimeError,AssertionError)):renderer.validate_success_payload(attacked)

def test_failed_recovery_ignores_old_positions_and_candidate_symbols(payload):
    failed=copy.deepcopy(payload);failed.update(status='error',generated_at='2026-10-23T16:00:00+08:00',error_type='EvidenceUnavailable',error='missing original IC package')
    for s in failed['signals'].values():
        for k in list(s):
            if 'contract' in k or k in {'put_current','put_target','v14_action_reason'}:s[k]='STALE_TARGET_SENTINEL'
    output='\n'.join(renderer.build_failure(failed,'',''))+renderer.build_failure_html(failed,'')
    assert 'STALE_TARGET_SENTINEL' not in output
    assert '尚未确认' in output
    with pytest.raises(ValueError):marker.marker_name(failed)

@pytest.mark.parametrize('suffix',['','-send-intent'])
def test_result_day_gate_uses_old_signal_day_not_attempt_day(payload,suffix):
    prefix=day_gate.marker_prefix(date(2026,10,22),'close_confirmed')
    artifacts={'artifacts':[{'name':prefix+'123456789abc'+suffix,'expired':False}]}
    if suffix:
        with pytest.raises(RuntimeError,match='uncertain'):result_gate.should_send_for_result(payload,artifacts,correction=False)
    else:assert result_gate.should_send_for_result(payload,artifacts,correction=False) is False

def test_other_day_marker_does_not_block_historical_day(payload):
    prefix=day_gate.marker_prefix(date(2026,10,23),'close_confirmed')
    artifacts={'artifacts':[{'name':prefix+'123456789abc','expired':False}]}
    assert result_gate.should_send_for_result(payload,artifacts,correction=False) is True

def test_explicit_correction_is_separate_from_normal_retry(payload):
    prefix=day_gate.marker_prefix(date(2026,10,22),'close_confirmed')
    artifacts={'artifacts':[{'name':prefix+'123456789abc-send-intent','expired':False}]}
    with pytest.raises(RuntimeError):result_gate.should_send_for_result(payload,artifacts,correction=False)
    assert result_gate.should_send_for_result(payload,artifacts,correction=True) is True

@pytest.mark.parametrize('event,mode,correction,external',[('schedule','close_confirmed','false','false'),('workflow_dispatch','realtime','false','false'),('workflow_dispatch','close_confirmed','true','false'),('workflow_dispatch','close_confirmed','false','true')])
def test_invalid_ledger_only_attempt_cannot_enable_smtp(event,mode,correction,external):
    with pytest.raises(ValueError):attempt.classify_attempt(event,'true',mode,correction,external)

def test_valid_ledger_only_attempt_disables_smtp():
    decision=attempt.classify_attempt('workflow_dispatch','true','close_confirmed','false','false')
    assert decision['smtp_enabled']=='false' and decision['ledger_only']=='true'

def test_invalid_archive_cannot_initialize_destination(tmp_path):
    dest=tmp_path/'ledger'
    with pytest.raises(Exception):restore.extract(b'corrupt official artifact',dest)
    assert not dest.exists() or not list(dest.rglob('*'))
