"""Read-only evidence checks; historical exact current-source equality lives in the receipt only."""
from pathlib import Path
import ast
import importlib
import json
import hashlib
import xml.etree.ElementTree as ET
import pytest

ROOT = Path(__file__).resolve().parent
OLD = ROOT / 'quant_research_runs/20261021_external_review_round2_v1'
FIXTURE = ROOT / 'tests/fixtures/icim_external_review_round2_20261021'
BINDING = {'source.py': 'a' * 64}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_1192_receipt_matches_actual_cases_and_46_files():
    receipt = read(OLD / 'final/receipt.json')
    tree = ET.parse(OLD / 'final/regression.xml').getroot()
    cases = tree.findall('.//testcase')
    assert len(cases) == receipt['counts']['tests'] == 1192
    for tag in ('failure', 'error', 'skipped'):
        assert tree.findall('.//' + tag) == []
    args = read(OLD / 'final/test_run.json')['files']
    files = [v for v in args if v.endswith('.py')]
    assert len(files) == len(set(files)) == 46
    assert args[-2:] == ['--deselect', next(iter(receipt['deselected']))]
    assert len(receipt['deselected']) == 1
    assert 'test_frozen_source_receipt_matches_current_bytes' in args[-1]
    assert 'Historical byte equality' in receipt['deselected'][args[-1]]


def test_historical_source_and_artifact_bindings_are_internally_consistent():
    receipt = read(OLD / 'final/receipt.json')
    before, after = [read(OLD / 'final' / ('source_' + stage + '.json')) for stage in ('before', 'after')]
    assert before == after
    assert len(before) == receipt['source_files'] == 845
    assert receipt['source_before_equals_after'] is True
    assert all(sha(OLD / 'final' / p) == h for p, h in receipt['artifacts'].items())
    assert 'quant_research_runs/20261021_external_review_round2_v1/final_acceptance.py' in before
    assert 'quant_research_runs/20261021_external_review_round2_v1/run_all.py' in before
    assert 'test_ic_im_external_review_round2_20261021.py' in before


def test_fixture_bytes_match_saved_raw_payloads_and_manifest():
    for p, h in read(FIXTURE / 'manifest.json').items():
        assert sha(FIXTURE / p) == sha(OLD / 'raw' / p) == h
    verification = read(OLD / 'real_identity_verification.json')
    for p, data in verification['source_files'].items():
        assert sha(OLD / 'raw' / p) == data['sha256']
        assert (OLD / 'raw' / p).stat().st_size == data['size']
    quote = read(FIXTURE / 'sse_december_identity.json')
    assert str(quote['date']) == '20261021'
    assert len(quote['list']) == 58
    events = read(FIXTURE / 'sse_adjustment_20260715.json')['result']
    assert len(events) == 114
    assert {v['ADJUST_DATE'] for v in events} == {'20260715'}
    assert {v['TIMESAVE'] for v in read(FIXTURE / 'sse_contract_master.json')['result']} == {'2026-10-20'}
    assert len(verification['live_adjusted_puts']) == 14
    assert all(v['engine_supported'] is False for v in verification['live_adjusted_puts'])


def test_old_display_test_changed_only_to_supply_the_holding_security_id():
    name = 'test_ic_im_external_review_display_cache_20261021.py'
    old = ast.parse((OLD / 'backup' / name).read_text(encoding='utf-8'))
    new = ast.parse((ROOT / name).read_text(encoding='utf-8'))
    removed = []
    for node in ast.walk(new):
        if isinstance(node, ast.Call):
            for kw in list(node.keywords):
                if kw.arg == 'security_id' and ast.unparse(kw.value) == "anchor['v14_core_put_security_id']":
                    removed.append(ast.unparse(kw.value))
                    node.keywords.remove(kw)
    assert removed == ["anchor['v14_core_put_security_id']"]
    assert ast.dump(old) == ast.dump(new)


def test_new_identity_dependency_is_importable_and_has_no_filesystem_write_api():
    identity = importlib.import_module('ic_im_sse_contract_identity_v1')
    assert callable(identity.validate_chain_rows)
    tree = ast.parse(Path(identity.__file__).read_text(encoding='utf-8'))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names.intersection({'open', 'Path', 'os', 'subprocess', 'requests'})


def test_preserved_runtime_backup_and_older_evidence_match_baseline():
    baseline = read(OLD / 'baseline.json')
    protected = {p: h for p, h in baseline['backed_up'].items() if p.startswith('runtime/')}
    assert protected
    # Live latest may advance independently; current exact bytes are recorded by audit_readonly.py.
    assert all(sha(OLD / 'backup' / p) == h for p, h in protected.items())
    assert all(sha(ROOT / p) == h for p, h in baseline['prior_evidence'].items())


def gate():
    import importlib.util
    path = ROOT / 'quant_research_runs/20261021_external_review_round2_redteam_v1/evidence_gate.py'
    spec = importlib.util.spec_from_file_location('round2_evidence_gate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def xml_fixture(path, *, tests=1, failures=0, errors=0, skipped=0, cases=1, outcome=None):
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', {k: str(v) for k, v in dict(tests=tests, failures=failures, errors=errors, skipped=skipped).items()})
    for i in range(cases):
        case = ET.SubElement(suite, 'testcase', name='case_' + str(i), classname='fixture')
        if outcome:
            ET.SubElement(case, outcome)
    ET.ElementTree(root).write(path, encoding='utf-8')
    return path


def test_current_gate_accepts_one_real_passing_case(tmp_path):
    counts = gate().validate_run(xml_fixture(tmp_path / 'result.xml'), process_exit_code=0,
                                 source_before=BINDING, source_after=BINDING)
    assert counts == dict(tests=1, failures=0, errors=0, skipped=0)


@pytest.mark.parametrize('config', [
    dict(tests=0, cases=0), dict(tests=2),
    dict(failures=1, outcome='failure'), dict(errors=1, outcome='error'),
    dict(skipped=1, outcome='skipped'), dict(outcome='failure'),
    dict(outcome='error'), dict(outcome='skipped'),
])
def test_current_gate_rejects_zero_exit_with_invalid_test_evidence(tmp_path, config):
    with pytest.raises(RuntimeError):
        gate().validate_run(xml_fixture(tmp_path / 'result.xml', **config), process_exit_code=0,
                            source_before=BINDING, source_after=BINDING)


@pytest.mark.parametrize('exit_code,before,after', [
    (1, BINDING, BINDING),
    (0, BINDING, {'source.py': 'b' * 64}),
    (0, {}, {}), (0, BINDING, {}),
])
def test_current_gate_rejects_process_or_binding_failure(tmp_path, exit_code, before, after):
    with pytest.raises(RuntimeError):
        gate().validate_run(xml_fixture(tmp_path / 'result.xml'), process_exit_code=exit_code,
                            source_before=before, source_after=after)


def receipt_fixture(directory):
    xml_fixture(directory / 'regression.xml')
    for name in ('source_before.json', 'source_after.json'):
        (directory / name).write_text(json.dumps(BINDING), encoding='utf-8')
    (directory / 'regression.txt').write_text('1 passed\n', encoding='utf-8')
    (directory / 'test_run.json').write_text(json.dumps({'exit_code': 0, 'files': ['test_example.py']}), encoding='utf-8')
    artifacts = {n: sha(directory / n) for n in ('regression.xml', 'regression.txt', 'source_before.json', 'source_after.json', 'test_run.json')}
    receipt = dict(exit_code=0, source_before_equals_after=True, source_files=1,
                   counts=dict(tests=1, failures=0, errors=0, skipped=0), artifacts=artifacts)
    (directory / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return receipt


def test_current_gate_accepts_consistent_receipt(tmp_path):
    receipt = receipt_fixture(tmp_path)
    assert gate().validate_receipt(tmp_path) == receipt['counts']


@pytest.mark.parametrize('mutation', ['source_flag', 'counts', 'source_count', 'missing_artifact', 'changed_artifact', 'changed_source', 'runner_exit'])
def test_current_gate_rejects_receipt_contradictions(tmp_path, mutation):
    receipt = receipt_fixture(tmp_path)
    if mutation == 'source_flag':
        receipt['source_before_equals_after'] = False
    elif mutation == 'counts':
        receipt['counts']['tests'] = 2
    elif mutation == 'source_count':
        receipt['source_files'] = 2
    elif mutation == 'missing_artifact':
        del receipt['artifacts']['test_run.json']
    elif mutation == 'changed_artifact':
        (tmp_path / 'regression.txt').write_text('tampered', encoding='utf-8')
    elif mutation == 'changed_source':
        (tmp_path / 'source_after.json').write_text(json.dumps({'source.py': 'b' * 64}), encoding='utf-8')
        receipt['artifacts']['source_after.json'] = sha(tmp_path / 'source_after.json')
    else:
        # A zero-exit receipt cannot overrule the underlying runner's recorded failure.
        (tmp_path / 'test_run.json').write_text(json.dumps({'exit_code': 1, 'files': ['test_example.py']}), encoding='utf-8')
        receipt['artifacts']['test_run.json'] = sha(tmp_path / 'test_run.json')
    (tmp_path / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(RuntimeError):
        gate().validate_receipt(tmp_path)
