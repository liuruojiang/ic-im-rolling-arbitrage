"""Independent history/provenance and acceptance-chain adversarial tests.

No network or formal writes. Corruption is confined to copied audit artifacts.
"""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'
PREV = ROOT / 'quant_research_runs/20261023_production_provenance_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate():
    spec = importlib.util.spec_from_file_location('_ma23_history_receipt_gate', RUN / 'evidence_gate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copied_receipt(tmp_path):
    target = tmp_path / 'copied_accepted_receipt'
    target.mkdir()
    for name in ['receipt.json', 'source_before.json', 'source_after.json', 'test_run.json',
                 'regression.xml', 'regression.txt', 'collection.json']:
        shutil.copyfile(PREV / 'accepted_01' / name, target / name)
    return target


def rebind_artifact(target, name):
    receipt = read(target / 'receipt.json')
    receipt['artifacts'][name] = sha(target / name)
    (target / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')


def synthetic_single_receipt(target, nodeid):
    from _pytest.junitxml import mangle_test_address
    target.mkdir()
    names = mangle_test_address(nodeid)
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests='1', failures='0', errors='0', skipped='0')
    ET.SubElement(suite, 'testcase', classname='.'.join(names[:-1]), name=names[-1])
    ET.ElementTree(root).write(target / 'regression.xml', encoding='utf-8')
    (target / 'regression.txt').write_text('synthetic schema case; no market data', encoding='utf-8')
    source = {Path(__file__).name: sha(Path(__file__))}
    for name in ['source_before.json','source_after.json']:
        (target / name).write_text(json.dumps(source), encoding='utf-8')
    (target / 'test_run.json').write_text(json.dumps({'exit_code':0,'files':[nodeid.split('::',1)[0]]}), encoding='utf-8')
    collection = {'collected':[nodeid],'selected':[nodeid],'executed':[nodeid],'deselected':[]}
    (target / 'collection.json').write_text(json.dumps(collection), encoding='utf-8')
    artifacts = ['source_before.json','source_after.json','test_run.json','collection.json','regression.xml','regression.txt']
    receipt = {'exit_code':0,'source_before_equals_after':True,'source_files':1,
        'counts':{'tests':1,'failures':0,'errors':0,'skipped':0},
        'artifacts':{name:sha(target/name) for name in artifacts}}
    (target / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')


@pytest.mark.parametrize('nodeid', [
    'test_m.py::test_x[a::b.c]',
    'cloud/tests/test_m.py::TestC::test_x[folder/a.py::fn]',
    'test_nested.py::Outer::Inner::test_method[case.with.dots]',
    'tests/test_win.py::test_x[C:\\tmp\\folder.a]',
    'test_unicode.py::test_contract[合约2703::日期]'])
def test_receipt_identity_mapping_preserves_class_cloud_and_parameter_boundaries(tmp_path, nodeid):
    target = tmp_path / 'synthetic_receipt'
    synthetic_single_receipt(target, nodeid)
    assert gate().validate_receipt(target)['tests'] == 1


def test_receipt_rejects_xml_test_identity_not_in_executed_collection(tmp_path):
    target = copied_receipt(tmp_path)
    tree = ET.parse(target / 'regression.xml')
    case = tree.getroot().find('.//testcase')
    case.set('name', 'test_synthetic_success_from_another_run')
    tree.write(target / 'regression.xml', encoding='utf-8', xml_declaration=True)
    rebind_artifact(target, 'regression.xml')
    # Counts, exit status and every artifact hash are internally consistent;
    # the XML identity contradicts the supposedly executed nodeid list.
    with pytest.raises(RuntimeError, match='identity|identit|用例|执行|XML|collection'):
        gate().validate_receipt(target)


def test_receipt_rejects_wrong_collection_identity_even_with_same_xml_count(tmp_path):
    target = copied_receipt(tmp_path)
    collection = read(target / 'collection.json')
    old = collection['selected'][0]
    replacement = 'test_unexecuted_synthetic_module.py::test_unexecuted_synthetic_case'
    for key in ['collected', 'selected', 'executed']:
        collection[key] = [replacement if node == old else node for node in collection[key]]
    (target / 'collection.json').write_text(json.dumps(collection), encoding='utf-8')
    rebind_artifact(target, 'collection.json')
    with pytest.raises(RuntimeError, match='identity|identit|用例|执行|XML|collection'):
        gate().validate_receipt(target)


def test_previous_accepted_receipt_has_matching_real_case_inventory():
    collection = read(PREV / 'accepted_01/collection.json')
    cases = ET.parse(PREV / 'accepted_01/regression.xml').getroot().findall('.//testcase')
    expected = set()
    for nodeid in collection['executed']:
        path, *parts = nodeid.split('::')
        module = path.removesuffix('.py').replace('/', '.').replace('\\', '.')
        expected.add(('.'.join([module, *parts[:-1]]), parts[-1]))
    assert {(c.get('classname'), c.get('name')) for c in cases} == expected
    assert gate().validate_receipt(PREV / 'accepted_01')['tests'] == 2982


def test_22_exclusions_have_not_grown_across_the_four_recent_rounds():
    rounds = ['20261022_free_source_integration_v1', '20261022_evidence_conflict_review_v1',
              '20261022_evidence_http_date_v1', '20261023_production_provenance_v1']
    hashes = {sha(ROOT / 'quant_research_runs' / name / 'exclusions.json') for name in rounds}
    assert len(hashes) == 1
    exclusions = read(PREV / 'exclusions.json')
    assert len(exclusions) == 22
    collection = read(PREV / 'accepted_01/collection.json')
    assert set(collection['deselected']) == set(exclusions)
    assert not set(collection['executed']) & set(exclusions)


def test_A_to_H_provenance_snapshots_match_the_original_accepted_code():
    provenance = read(PREV / 'delivery/A_H_provenance.json')
    assert provenance['status'] == 'PASS'
    for snapshot in provenance['snapshots']:
        assert sha(Path(snapshot['path'])) == snapshot['sha256']
    rounds = ROOT / 'quant_research_runs'
    free = read(rounds / '20261022_free_source_integration_v1/accepted_02/source_after.json')
    conflict = read(rounds / '20261022_evidence_conflict_review_v1/accepted_03/source_after.json')
    date_fix = read(rounds / '20261022_evidence_http_date_v1/accepted_02/source_after.json')
    assert provenance['snapshots'][1]['sha256'] == free['poe_ic_im_mainline_v1_4_bot.py']
    assert provenance['snapshots'][2]['sha256'] == conflict['poe_ic_im_mainline_v1_4_bot.py'] == date_fix['poe_ic_im_mainline_v1_4_bot.py']


def static_local_dependencies(entries):
    todo, seen = list(entries), set()
    while todo:
        name = todo.pop()
        path = ROOT / (name + '.py')
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            names = [a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import) else (
                [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
            todo.extend(n for n in names if (ROOT / (n + '.py')).is_file())
    return {name + '.py' for name in seen}


def test_current_28_file_pin_covers_actual_static_capture_dependency_closure():
    pin = read(PREV / 'delivery/capture_source_pin.json')['files']
    dependencies = static_local_dependencies(['capture_ic_im_v1_4_inputs',
        'poe_ic_im_mainline_v1_4_bot', 'poe_ic_im_v1_4_state', 'ic_im_evidence_adapter_v1'])
    assert len(dependencies) == 16 and len(pin) == 28
    assert dependencies <= set(pin)
    assert 'im_put_policy.py' in dependencies


def test_loaded_inventory_includes_actual_nonprefixed_production_dependency():
    import capture_ic_im_v1_4_inputs as capture
    import im_put_policy  # real production module, not an injected fake.
    assert Path(im_put_policy.__file__).resolve() == ROOT / 'im_put_policy.py'
    assert 'im_put_policy.py' in capture.loaded_sources()


def test_pin_missing_real_loaded_im_policy_is_rejected(tmp_path):
    import capture_ic_im_v1_4_inputs as capture
    import im_put_policy
    pin = read(PREV / 'delivery/capture_source_pin.json')
    pin['files'] = {n:sha(ROOT/n) for n in pin['files'] if n != 'im_put_policy.py'}
    path = tmp_path / 'deliberately_incomplete_pin.json'
    path.write_text(json.dumps(pin), encoding='utf-8')
    active = capture.SourcePin(path)
    assert Path(im_put_policy.__file__).resolve().is_file()
    with pytest.raises(capture.SourcePinError, match='im_put_policy'):
        active.verify(capture.loaded_sources())


def test_historical_numeric_functions_and_inputs_remain_bound_to_prior_versions():
    prior_bot = ROOT / 'quant_research_runs/20261022_free_source_integration_v1/backup/poe_ic_im_mainline_v1_4_bot.py'
    def selected(path):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(
            path.read_text(encoding='utf-8-sig')).body if isinstance(node, ast.FunctionDef)
                and node.name in {'_bs_price_delta', 'implied_volatility', 'fetch_sina_option_closes',
                                  'fetch_option_dated_open', 'fetch_510500_historical_price'}}
    old, current = selected(prior_bot), selected(ROOT / 'poe_ic_im_mainline_v1_4_bot.py')
    assert old and old == current
    source = read(PREV / 'accepted_01/source_after.json')
    protected = {n: h for n, h in source.items() if n.startswith(
        'quant_research_runs/20261022_historical_calendar_repair_v1/')}
    assert protected
    assert all(sha(ROOT / n) == h for n, h in protected.items())
    # The calendar extension DID alter IC historically; this is not zero impact.
    record = (ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/record.md').read_text(encoding='utf-8')
    assert '35.3924' in record and '34.8145' in record
