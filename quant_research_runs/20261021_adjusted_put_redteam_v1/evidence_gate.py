"""Strict test-evidence acceptance for this audit; historical receipts remain immutable."""
from pathlib import Path
import hashlib
import json
import re
import xml.etree.ElementTree as ET

def _reject(reason):
    raise RuntimeError('audit evidence rejected: ' + reason)

def _source_map(value):
    if (not isinstance(value, dict) or not value
            or any(not isinstance(k, str) or not k or not isinstance(v, str)
                   or not re.fullmatch(r'[0-9a-f]{64}', v) for k, v in value.items())):
        _reject('empty or invalid source binding')

def _count(suite, name):
    value = suite.get(name)
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value):
        _reject('missing or invalid XML count: ' + name)
    return int(value)

def validate_run(xml_path, *, process_exit_code, source_before, source_after):
    if type(process_exit_code) is not int or process_exit_code != 0:
        _reject('test process did not exit zero')
    _source_map(source_before)
    _source_map(source_after)
    if source_before != source_after:
        _reject('source changed during test execution')
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError, TypeError) as exc:
        _reject('missing or malformed XML: ' + type(exc).__name__)
    if root.tag == 'testsuite':
        suites = [root]
    elif root.tag == 'testsuites':
        suites = list(root)
    else:
        _reject('invalid XML root')
    if not suites or any(s.tag != 'testsuite' or s.find('testsuite') is not None for s in suites):
        _reject('empty or unsupported XML suite structure')
    keys = ['tests', 'failures', 'errors', 'skipped']
    counts = dict.fromkeys(keys, 0)
    seen = set()
    for suite in suites:
        declared = {key: _count(suite, key) for key in keys}
        cases = suite.findall('testcase')
        actual = dict(tests=len(cases), failures=0, errors=0, skipped=0)
        for case in cases:
            identity = (case.get('classname'), case.get('name'))
            if not all(identity) or identity in seen:
                _reject('missing or duplicate test identity')
            seen.add(identity)
            statuses = [name for name in ['failure', 'error', 'skipped'] if case.find(name) is not None]
            if len(statuses) > 1:
                _reject('contradictory testcase status')
            if statuses:
                actual[dict(failure='failures', error='errors', skipped='skipped')[statuses[0]]] += 1
        if actual != declared:
            _reject('testcase counts disagree with XML summary')
        for key in keys:
            counts[key] += actual[key]
    if root.tag == 'testsuites':
        for key in keys:
            if key in root.attrib and _count(root, key) != counts[key]:
                _reject('root XML summary mismatch')
    for tag, key in [('testcase', 'tests'), ('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')]:
        if len(root.findall('.//' + tag)) != counts[key]:
            _reject('unexpected or nested XML status elements')
    if counts['tests'] <= 0 or any(counts[key] for key in ['failures', 'errors', 'skipped']):
        _reject('requires positive test count and zero failures/errors/skips')
    return counts

def validate_receipt(directory):
    directory = Path(directory)
    try:
        receipt = json.loads((directory / 'receipt.json').read_text(encoding='utf-8'))
        before = json.loads((directory / 'source_before.json').read_text(encoding='utf-8'))
        after = json.loads((directory / 'source_after.json').read_text(encoding='utf-8'))
        test_run = json.loads((directory / 'test_run.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        _reject('receipt/source files unavailable: ' + type(exc).__name__)
    if not isinstance(receipt, dict):
        _reject('receipt schema invalid')
    if (not isinstance(test_run, dict) or type(test_run.get('exit_code')) is not int
            or test_run['exit_code'] != 0 or test_run['exit_code'] != receipt.get('exit_code')):
        _reject('test runner exit status contradicts receipt')
    counts = validate_run(directory / 'regression.xml', process_exit_code=receipt.get('exit_code'),
                          source_before=before, source_after=after)
    if (receipt.get('source_before_equals_after') is not True
            or type(receipt.get('source_files')) is not int or receipt['source_files'] != len(before)
            or receipt.get('counts') != counts):
        _reject('receipt contradicts actual source binding or counts')
    required = {'regression.xml', 'regression.txt', 'source_before.json', 'source_after.json', 'test_run.json'}
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != required:
        _reject('missing or unexpected receipt artifact binding')
    for name, expected in artifacts.items():
        path = directory / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            _reject('artifact hash mismatch: ' + name)
    return counts
