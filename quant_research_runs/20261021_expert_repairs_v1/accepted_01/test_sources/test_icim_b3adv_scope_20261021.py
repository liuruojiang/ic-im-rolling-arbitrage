"""Independent scope/evidence probes; synthetic subprocesses and read-only inventory."""
import ast
from copy import deepcopy
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as old_call_seed

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / 'quant_research_runs/20261021_batch3_adversarial_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observation(name, value):
    stage = os.environ.get('ICIM_AUDIT_STAGE', 'manual')
    target = AUDIT/'scope/observations'/stage/name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def skeleton(tmp_path):
    """Copy only the current runners into an isolated project, never old evidence."""
    project = tmp_path/'project'
    scripts = project/'quant_research_runs/probe'
    scripts.mkdir(parents=True)
    for name in ['run_checks.py', 'run_independent.py', 'evidence_gate.py', 'pytest.ini']:
        shutil.copy2(AUDIT/name, scripts/name)
    (project/'docs').mkdir()
    (project/'docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json').write_bytes(
        json.dumps({'required_local_tests': []}).encode())
    return project, scripts


def receipt_copy(tmp_path, declared):
    """Self-consistent evidence with one explicitly deselected synthetic test."""
    directory=tmp_path/'receipt'
    directory.mkdir()
    counts=dict(tests=1,failures=0,errors=0,skipped=0)
    source={'test_probe.py':'1'*64}
    values={
        'source_before.json':source,'source_after.json':source,
        'test_run.json':dict(exit_code=0,files=['test_probe.py','--deselect','test_probe.py::test_two']),
        'collection.json':dict(collected=['test_probe.py::test_one','test_probe.py::test_two'],
            selected=['test_probe.py::test_one'],deselected=['test_probe.py::test_two'],
            executed=['test_probe.py::test_one'])}
    for name,value in values.items():
        (directory/name).write_bytes(json.dumps(value).encode())
    (directory/'regression.xml').write_bytes(b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="test_probe" name="test_one" /></testsuite></testsuites>')
    (directory/'regression.txt').write_bytes(b'1 passed, 1 deselected')
    receipt=dict(exit_code=0,source_before_equals_after=True,source_files=1,counts=counts,
        deselected=declared,artifacts={name:sha(directory/name) for name in
            ['regression.xml','regression.txt','source_before.json','source_after.json','test_run.json','collection.json']})
    (directory/'receipt.json').write_bytes(json.dumps(receipt).encode())
    spec=importlib.util.spec_from_file_location('_scope_current_evidence_gate',AUDIT/'evidence_gate.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return directory,module


@pytest.mark.parametrize('declared',[{}, {'test_probe.py::some_other_case':'not actually excluded'}])
def test_declared_final_exclusion_set_must_match_actual_selection(tmp_path,declared):
    directory,gate=receipt_copy(tmp_path,declared)
    with pytest.raises(RuntimeError,match='exclusion|deselect|selection'):
        gate.validate_receipt(directory)


def test_declared_final_exclusion_positive_control_is_accepted(tmp_path):
    directory,gate=receipt_copy(tmp_path,{'test_probe.py::test_two':'explicit scope exception'})
    assert gate.validate_receipt(directory)['tests']==1


@pytest.mark.parametrize('inherited', [
    '-k visible_pass', '--deselect=test_selection.py::test_hidden_failure'])
def test_inherited_pytest_selection_cannot_hide_a_requested_failure(tmp_path, inherited):
    project, scripts = skeleton(tmp_path)
    (project/'test_selection.py').write_bytes(
        b'def test_visible_pass():\n    assert True\n\ndef test_hidden_failure():\n    assert False, "requested test must execute"\n')
    env = dict(os.environ, PYTEST_ADDOPTS=inherited, ICIM_AUDIT_STAGE='child')
    result = subprocess.run([sys.executable, str(scripts/'run_checks.py'), 'test_selection.py'],
                            cwd=project, env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=40)
    xml = scripts/'child/regression.xml'
    observation('inherited_'+('k' if inherited.startswith('-k') else 'deselect')+'.json',
                dict(returncode=result.returncode, implicit_options=inherited,
                     xml=xml.read_text(encoding='utf-8') if xml.exists() else None,
                     child_output=result.stdout[-1500:]))
    assert result.returncode != 0, 'implicit PYTEST_ADDOPTS hid a requested failing test'
    suite=ET.parse(xml).getroot().find('testsuite')
    assert suite.get('tests')=='2' and suite.get('failures')=='1'


@pytest.mark.parametrize('config', ['pytest.ini', 'pyproject.toml'])
def test_external_collection_configuration_cannot_hide_requested_test_functions(tmp_path, config):
    project, scripts = skeleton(tmp_path)
    (project/'test_selection.py').write_bytes(
        b'def test_visible_pass():\n    assert True\n\ndef test_hidden_failure():\n    assert False, "requested test must execute"\n')
    (project/config).write_bytes((
        '[pytest]\npython_functions = test_visible_*\n' if config == 'pytest.ini' else
        '[tool.pytest.ini_options]\npython_functions = ["test_visible_*"]\n').encode())
    env = dict(os.environ)
    env.pop('PYTEST_ADDOPTS', None)
    result = subprocess.run([sys.executable, str(scripts/'run_independent.py'), 'config_selection',
                             'test_selection.py'], cwd=project, env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=40)
    accepted = scripts/'config_selection/receipt.json'
    observation('config_'+config.replace('.','_')+'.json', dict(returncode=result.returncode,
                accepted_receipt=accepted.read_text(encoding='utf-8') if accepted.exists() else None,
                child_output=result.stdout[-1200:],child_error=result.stderr[-800:]))
    assert result.returncode != 0 and not accepted.exists(), 'unbound config hid a requested failure yet received strict PASS'
    suite=ET.parse(scripts/'config_selection/regression.xml').getroot().find('testsuite')
    assert suite.get('tests')=='2' and suite.get('failures')=='1'


@pytest.mark.parametrize('form', ['bare', 'dot', 'absolute', 'dot_node'])
def test_independent_source_binding_normalizes_selected_test_path(tmp_path, form):
    project, scripts = skeleton(tmp_path)
    selected = project/'test_icim_b3adv_probe.py'
    selected.write_bytes(b'def test_one():\n    assert True\n')
    arg = {'bare':selected.name, 'dot':'./'+selected.name,
           'absolute':str(selected), 'dot_node':'./'+selected.name+'::test_one'}[form]
    tree = ast.parse((scripts/'run_independent.py').read_text(encoding='utf-8'))
    functions = ast.Module(body=[node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name in {'sha','sources'}],
                           type_ignores=[])
    namespace = dict(ROOT=project, HERE=scripts, tests=[arg], hashlib=hashlib, Path=Path)
    exec(compile(functions, str(scripts/'run_independent.py'), 'exec'), namespace)
    bound = namespace['sources']()
    assert bound.get(selected.name) == sha(selected), 'executed test module is omitted from source binding'


@pytest.mark.parametrize('form', ['dot', 'absolute'])
def test_executed_test_self_change_is_rejected_even_with_prefixed_path(tmp_path, form):
    project, scripts = skeleton(tmp_path)
    selected = project/'test_icim_b3adv_self_change.py'
    selected.write_bytes(b'from pathlib import Path\ndef test_self_change():\n'
                         b'    p=Path(__file__)\n    p.write_bytes(p.read_bytes()+b"\\n# changed during run\\n")\n')
    arg = './'+selected.name if form == 'dot' else str(selected)
    env = dict(os.environ)
    env.pop('PYTEST_ADDOPTS', None)
    result = subprocess.run([sys.executable, str(scripts/'run_independent.py'), 'self_change', arg],
                            cwd=project, env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=40)
    accepted = scripts/'self_change/receipt.json'
    observation('self_change_'+form+'.json', dict(returncode=result.returncode,
                accepted_receipt=accepted.read_text(encoding='utf-8') if accepted.exists() else None,
                child_output=result.stdout[-1000:], child_error=result.stderr[-1000:]))
    assert result.returncode != 0 and not accepted.exists(), 'unbound executed test changed yet received a strict PASS'
    assert '1 passed' in result.stdout and 'source changed' in result.stderr


def test_local_call_inventory_uses_verified_state_and_preserves_journal_bytes():
    directory = ROOT/'runtime/ic_im_v1_4_r1'
    paths = sorted((directory/'journal').glob('*.json'))
    assert paths
    before = {str(p.relative_to(ROOT)):sha(p) for p in paths}
    inventory = []
    for path in paths:
        record = json.loads(path.read_text(encoding='utf-8'))
        state._validate_record(record)
        im = record['products']['IM']
        inventory.append(dict(sequence=record['sequence'], day=record['verified_day'],
            verified_contract=im.get('verified_call_contract'),
            verified_qty=im.get('verified_call_contracts_normalized'),
            legacy_contract=im.get('call_contract'),legacy_equivalent=im.get('call_equivalent_units')))
        assert im.get('verified_call_contract') is None
        assert float(im.get('verified_call_contracts_normalized', 0)) == 0
    after = {str(p.relative_to(ROOT)):sha(p) for p in paths}
    observation('inventory.json',dict(nodes=len(paths),inventory=inventory,
                                    source_before=before,source_after=after))
    assert before == after
    assert any(item['legacy_contract'] for item in inventory), 'control must distinguish retained legacy fields'


@pytest.mark.parametrize('last', [0., float('nan')])
def test_positive_ask_does_not_turn_unpriced_legacy_call_into_new_entry(monkeypatch, last):
    old_call_seed.wire(monkeypatch, held=True, last=last)
    with pytest.raises(RuntimeError, match='有限正价'):
        bot.build_live_trade_signal('IM', now=old_call_seed.CLOCK, mode='close')


@pytest.mark.parametrize('position', [0., -1., float('nan')])
def test_unconfirmed_legacy_oi_cannot_open_replacement_call(monkeypatch, position):
    old_call_seed.wire(monkeypatch, held=True, position=position)
    with pytest.raises(RuntimeError, match='正持仓量'):
        bot.build_live_trade_signal('IM', now=old_call_seed.CLOCK, mode='close')


@pytest.mark.parametrize('bid,ask', [(102.,101.), (99.,float('inf'))])
def test_crossed_or_infinite_quote_does_not_support_legacy_call_close(monkeypatch,bid,ask):
    old_call_seed.wire(monkeypatch, held=True, bid=bid, ask=ask)
    with pytest.raises(RuntimeError, match='双边有效报价'):
        bot.build_live_trade_signal('IM', now=old_call_seed.CLOCK, mode='close')


def test_legacy_buyback_positive_control_never_calls_new_call_selection(monkeypatch):
    old_call_seed.wire(monkeypatch, held=True)
    signal = bot.build_live_trade_signal('IM', now=old_call_seed.CLOCK, mode='close')
    assert signal['call_action'] == 'CLOSE_CALL'
    assert signal['call_target_qty_normalized'] == 0.
    assert signal['call_target_contract'] is None
    state.validate_im_option_values(signal)


def production_closure():
    queue = [ROOT/name for name in ['poe_ic_im_mainline_v1_4_bot.py','poe_ic_im_v1_4_server.py',
        'poe_ic_im_v1_4_state.py','run_ic_im_v1_4_github_digest.py']]
    seen,imports,strings,dynamic = set(),set(),[],[]
    while queue:
        path = queue.pop()
        if path in seen:continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names=[]
            if isinstance(node,ast.Import):names=[a.name for a in node.names]
            if isinstance(node,ast.ImportFrom) and node.module:names=[node.module]
            for name in names:
                imports.add(name)
                candidate=ROOT/(name.split('.')[0]+'.py')
                if candidate.is_file():queue.append(candidate)
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                strings.append((path.name,node.value))
            if isinstance(node,ast.Call):
                target=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else None
                if target in {'__import__','exec','eval','import_module','spec_from_file_location',
                              'run_module','run_path'}:
                    dynamic.append((path.name,node.lineno,ast.unparse(node)))
    return seen,imports,strings,dynamic


def test_production_dynamic_import_and_config_paths_do_not_reach_put_candidates():
    paths,imports,strings,dynamic=production_closure()
    assert len(paths) >= 15
    research_dirs=[ROOT/'quant_research_runs'/name for name in [
        '20261021_ic_wait_etf_listing_v1','20261021_put_monday_close_v1',
        '20261021_put_monday_settlement_frozen_v1','20261021_put_after_settlement_monday_v1']]
    candidate_names={p.stem for directory in research_dirs for p in directory.glob('*.py')}
    assert not {name for name in imports if name.split('.')[-1] in candidate_names}
    assert not [(path,value) for path,value in strings if any(d.name in value for d in research_dirs)]
    assert dynamic == [], 'a dynamic local loader must be separately resolved before claiming isolation'
    observation('production_closure.json',dict(sources={str(p.relative_to(ROOT)):sha(p) for p in paths},
        research_modules=sorted(candidate_names),dynamic_calls=dynamic))


def test_candidate_research_and_specs_still_match_the_pre_audit_baseline():
    baseline=json.loads((AUDIT/'baseline.json').read_text(encoding='utf-8'))
    names=['20261021_ic_wait_etf_listing_v1','20261021_put_monday_close_v1',
           '20261021_put_monday_settlement_frozen_v1','20261021_put_after_settlement_monday_v1']
    protected={path:digest for path,digest in baseline['protected_prior_evidence'].items()
               if any('/'+name+'/' in path for name in names)}
    protected.update({path:digest for path,digest in baseline['backup'].items()
        if path.startswith('docs/') and ('wait_etf_listing' in path or 'put_monday' in path
                                       or 'put_after_settlement_monday' in path)})
    assert len(protected) >= 28
    assert all((ROOT/path).is_file() and sha(ROOT/path)==digest for path,digest in protected.items())
    observation('frozen_research.json',protected)


def test_formal_reset_keeps_nearest_expiry_instead_of_adopting_minimum_three_months(monkeypatch):
    day=date(2026,10,21)
    monkeypatch.setenv('ICIM_PUT_MIN_MONTHS','3')
    monkeypatch.setenv('ICIM_PUT_WAIT_ETF_LISTING','1')
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202612','202703'])
    monkeypatch.setattr(bot,'_now_beijing',lambda:old_call_seed.CLOCK)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *_:.02)
    requests=[]
    def fetch(month,reference):
        requests.append((month,reference))
        return pd.DataFrame([dict(contract='510500P2612M07500',security_id='10012609',
             strike=7.5,last=.2)]),dict(date='20261021',time='150000',source='synthetic')
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',fetch)
    selected=bot.select_ic_put_for_reset(day,8.,8000.,.25)
    assert selected['expiry'] == date(2026,12,23)
    assert selected['contract'] == '510500P2612M07500'
    assert requests == [('2612',day)]
    mo=pd.DataFrame([dict(instrument='MO'+month+'-P-8000',lastprice=100.,volume=10.,position=20.)
                     for month in ['2612','2703']])
    result=bot.select_im_put_for_reset(mo,day,8000.)
    assert result['instrument'] == 'MO2612-P-8000'
    assert result['expiry'] < (pd.Timestamp(day)+pd.DateOffset(months=3)).date()
