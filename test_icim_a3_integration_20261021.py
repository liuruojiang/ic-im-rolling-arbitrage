"""Independent A3 integration/evidence audit. Production state is never opened."""
from copy import deepcopy
from datetime import date
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

import ic_im_adjusted_put_migration_v1 as migration
import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_ic_im_v1_4_policy as policy_seed
import test_icim_round2_audit_lifecycle_20261021 as lifecycle

ROOT = Path(__file__).resolve().parent
OLD = ROOT / 'quant_research_runs/20261021_adjusted_put_migration_v1'
AUDIT = ROOT / 'quant_research_runs/20261021_adjusted_put_redteam_v1'
FIXTURE = ROOT / 'tests/fixtures/icim_external_review_round2_20261021'
DAY = date(2026, 10, 21)
A = '510500P2612A07500'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate():
    spec = importlib.util.spec_from_file_location('a3_evidence_gate', AUDIT / 'evidence_gate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_1412_receipt_matches_xml_and_all_bound_artifacts():
    directory = OLD / 'final_verified'
    receipt = read(directory / 'receipt.json')
    counts = gate().validate_receipt(directory)
    root = ET.parse(directory / 'regression.xml').getroot()
    assert counts == dict(tests=1412, failures=0, errors=0, skipped=0)
    assert len(root.findall('.//testcase')) == 1412
    assert receipt['source_files'] == 885
    assert receipt['source_before_equals_after'] is True
    assert set(receipt['deselected']) == {
        'test_icim_redteam_integration_20261021.py::test_frozen_source_receipt_matches_current_bytes',
        'test_icim_round2_audit_lifecycle_20261021.py::test_historical_adapter_retains_id_already_bound_to_requested_series',
        'test_icim_round2_audit_lifecycle_20261021.py::test_legal_standard_core_replay_crosses_strict_boundary',
    }


def test_old_source_receipt_is_bound_to_audit_start_bytes_not_future_repaired_bytes():
    bound = read(OLD / 'final_verified/source_after.json')
    baseline = read(AUDIT / 'baseline.json')
    for name, expected in bound.items():
        if name in baseline['backup']:
            assert sha(AUDIT / 'backup' / name) == expected
        else:
            # Old runner/fixture bytes are immutable; unlike production source,
            # these files are not repaired during the new audit.
            assert sha(ROOT / name) == expected


def test_previous_6795_outputs_and_465_prior_evidence_remain_unchanged():
    baseline = read(OLD / 'baseline.json')
    assert len(baseline['protected_outputs']) == 6795
    assert len(baseline['protected_prior_evidence']) == 465
    for key in ['protected_outputs', 'protected_prior_evidence']:
        assert not [name for name, expected in baseline[key].items() if sha(ROOT / name) != expected]


@pytest.mark.parametrize('artifact', ['regression.xml','regression.txt','source_before.json',
                                     'source_after.json','test_run.json'])
def test_changed_evidence_artifact_cannot_reuse_old_success_receipt(tmp_path, artifact):
    copied = tmp_path/'receipt'
    shutil.copytree(OLD/'final_verified',copied)
    path = copied/artifact
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(RuntimeError,match='audit evidence rejected'):
        gate().validate_receipt(copied)


def test_old_capability_probes_remain_original_and_explicitly_deselected():
    name = 'test_icim_round2_audit_lifecycle_20261021.py'
    assert sha(ROOT/name) == sha(AUDIT/'backup'/name)
    text = (ROOT/name).read_text(encoding='utf-8')
    functions = {node.name:node for node in ast.parse(text).body if isinstance(node,ast.FunctionDef)}
    run = read(OLD/'final_verified/test_run.json')['files']
    receipt = read(OLD/'final_verified/receipt.json')
    for function in ['test_historical_adapter_retains_id_already_bound_to_requested_series',
                     'test_legal_standard_core_replay_crosses_strict_boundary']:
        assert function in functions
        assert any(isinstance(node,ast.Assert) for node in ast.walk(functions[function]))
        node_id = name+'::'+function
        assert node_id in receipt['deselected'] and node_id in run


def test_new_production_import_dependencies_are_bound_but_not_falsely_deployed():
    proof = read(OLD/'final_verification.json')
    bound = read(OLD/'final_verified/source_after.json')
    for name in ['poe_ic_im_mainline_v1_4_bot.py','ic_im_adjusted_put_migration_v1.py',
                 'ic_im_sse_contract_identity_v1.py']:
        assert proof['production_dependency_hashes'][name] == bound[name]
    assert proof['deployed'] is False
    assert proof['a_engine_supported'] is False
    assert proof['historical_dates_fully_covered'] is False


@pytest.mark.parametrize('field', ['v14_core_put_contract','post_put_contract','put_current_contract',
    'put_target_contract','v14_short_put_contract','v14_profit_old_contract','v14_profit_reentry_contract'])
@pytest.mark.parametrize('permission', [False, True])
def test_attached_preview_cannot_authorize_a_supported_contract(field,permission):
    value = {field:A,'migration_preview':dict(engine_supported=permission,
                                              ledger_write_authorized=permission)}
    with pytest.raises(RuntimeError,match='adjusted contract master'):
        execution.require_supported_ic_contracts('IC',value)


@pytest.mark.parametrize('boundary',['builder','policy','new_signal','store'])
def test_real_boundaries_reject_a_identity_despite_preview(tmp_path,monkeypatch,boundary):
    store,record = lifecycle.recent_store(tmp_path/'ledger')
    anchors = state.anchors_from_record(record)
    source = deepcopy(anchors['IC'])
    source['v14_core_put_contract'] = A
    source['migration_preview'] = dict(engine_supported=True,ledger_write_authorized=True)
    before = lifecycle.snapshot(store.root)
    with pytest.raises(RuntimeError,match='adjusted contract master'):
        if boundary == 'builder':
            monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',{**anchors,'IC':source})
            monkeypatch.setattr(bot,'live_proxy',lambda *args:dict(history_date=DAY))
            bot.build_live_trade_signal('IC',lifecycle.CLOCK,mode='close')
        elif boundary == 'policy':
            policy.apply_policy('IC',policy_seed._signal('IC',market_date=DAY),source,
                                candidate={'tradable':False})
        else:
            signals = {p:ledger._signal(p,anchors[p],DAY) for p in state.PRODUCTS}
            signals['IC']['v14_core_put_contract'] = A
            signals['IC']['migration_preview'] = source['migration_preview']
            if boundary == 'new_signal':
                state.validate_v14_new_signal(signals['IC'],'IC')
            else:
                store.append_confirmed_signals(record,signals)
    assert lifecycle.snapshot(store.root) == before
    assert state.StateStore(store.root).load_latest()['digest'] == record['digest']


def test_unsupported_a_candidate_cannot_become_a_policy_opening_plan():
    anchor = {**policy.default_extension('IC'),'post_core_contract':'IC2612'}
    signal = policy_seed._signal('IC',market_date=DAY,next_trade_date=date(2026,10,22))
    candidate = policy_seed._candidate('IC',contract=A,security_id='10011470',
                                       expiry=date(2026,12,23))
    candidate['migration_preview'] = dict(engine_supported=False,ledger_write_authorized=False)
    with pytest.raises(RuntimeError,match='adjusted|supported|identity'):
        policy.apply_policy('IC',signal,anchor,candidate=candidate)


def test_prepared_a_candidate_cannot_commit_through_actual_temporary_store(tmp_path):
    store,record = lifecycle.recent_store(tmp_path/'ledger')
    anchors = state.anchors_from_record(record)
    before = lifecycle.snapshot(store.root)
    candidate = policy_seed._candidate('IC',contract=A,security_id='10011470',
                                       expiry=date(2026,12,23))
    with pytest.raises(RuntimeError,match='adjusted|supported|identity'):
        # Before the policy repair this reaches the StateStore A gate; after
        # repair it must stop already at policy output. Either way, no write.
        signal = ledger._signal('IC',anchors['IC'],DAY,candidate=candidate,
                                extra={'momentum_120':.02})
        other = ledger._signal('IM',anchors['IM'],DAY)
        store.append_confirmed_signals(record,{'IC':signal,'IM':other})
    assert lifecycle.snapshot(store.root) == before
    assert state.StateStore(store.root).load_latest()['digest'] == record['digest']


@pytest.mark.parametrize('route',['roll','profit3x'])
def test_other_consumed_a_candidates_cannot_become_unsupported_plans(route):
    anchor = {**policy.default_extension('IC'),'post_core_contract':'IC2612'}
    signal = policy_seed._signal('IC',market_date=DAY,next_trade_date=date(2026,10,22))
    candidate = policy_seed._candidate('IC',contract=A,security_id='10011470',
                                       expiry=date(2026,12,23))
    if route == 'roll':
        anchor.update(v14_route_state='short_put',v14_short_put_contract='510500P2611M07500',
                      v14_short_put_security_id='10012098',v14_short_put_qty_normalized=10.,
                      v14_short_put_entry_premium=.4,v14_short_put_entry_price_day='2026-10-20',
                      v14_short_put_cost_basis='confirmed_dated_open',
                      v14_short_put_expiry=date(2026,11,25))
        signal['v14_short_put_mark'] = .1
    else:
        anchor.update(v14_core_put_contract='510500P2612M07500',v14_core_put_security_id='10012099',
                      v14_core_put_qty=5,v14_core_put_entry_premium=.4,v14_core_put_profit3x_eligible=True)
        signal.update(v14_core_put_contract='510500P2612M07500',v14_core_put_security_id='10012099',
                      v14_core_put_qty=5,put_target_core_qty=5,v14_core_put_mark=1.3,
                      v14_profit_plan_contract=A,v14_profit_plan_security_id='10011470',
                      v14_profit_plan_qty=5)
        candidate = {'tradable':False}
    with pytest.raises(RuntimeError,match='adjusted|supported|identity'):
        policy.apply_policy('IC',signal,anchor,candidate=candidate,roll_candidate=candidate)


@pytest.mark.parametrize('product',['IC','IM'])
def test_standard_ic_and_im_candidates_remain_eligible(product):
    anchor = {**policy.default_extension(product),'post_core_contract':product+'2612'}
    signal = policy_seed._signal(product,market_date=DAY,next_trade_date=date(2026,10,22))
    result = policy.apply_policy(product,signal,anchor,candidate=policy_seed._candidate(product))
    assert result['v14_action'] == 'ENTER_SHORT_PUT'
    assert result['v14_short_put_open_pending']['product'] == product


def test_unused_a_candidate_does_not_block_independent_verified_lifecycle():
    expiry = date(2026,10,28)
    anchor = {**policy.default_extension('IC'),'post_core_contract':'IC2612'}
    anchor.update(v14_route_state='short_put',v14_short_put_contract='510500P2610M07500',
                  v14_short_put_security_id='10012098',v14_short_put_qty_normalized=10.,
                  v14_short_put_entry_premium=.4,v14_short_put_entry_price_day='2026-10-20',
                  v14_short_put_cost_basis='confirmed_dated_open',v14_short_put_expiry=expiry)
    signal = policy_seed._signal('IC',market_date=expiry,next_trade_date=date(2026,10,29),
                                v14_model_settlement_confirmed=True,
                                v14_model_settlement_outcome='expired_worthless')
    candidate = policy_seed._candidate('IC',contract=A,security_id='10011470',
                                       expiry=date(2026,12,23),tradable=False)
    result = policy.apply_policy('IC',signal,anchor,candidate=candidate)
    assert result['v14_action'] == 'SHORT_PUT_EXPIRE_WORTHLESS'
    assert result['v14_short_put_contract'] is None


def test_canonical_identity_hash_is_not_confused_with_raw_file_hash(monkeypatch):
    path = FIXTURE/'sse_contract_master.json'
    payload = read(path)
    canonical = migration.payload_hash(payload)
    assert canonical != sha(path)  # Serialization/whitespace are different evidence.
    assert canonical == migration.payload_hash(dict(reversed(list(payload.items()))))
    master = migration.master_terms(payload,as_of=date(2026,10,20))
    monkeypatch.setattr(bot,'fetch_sse_510500_dated_master',lambda _:master)
    prices = pd.Series([.25],index=pd.to_datetime(['2026-10-20']))
    prices.attrs['source']='synthetic_price_only'
    monkeypatch.setattr(bot,'fetch_option_closes',lambda _:prices)
    _,stamp = bot.fetch_sse_existing_put_historical_quote('510500P2612M07500','10012099',date(2026,10,20))
    assert stamp['identity_payload_sha256'] == canonical
