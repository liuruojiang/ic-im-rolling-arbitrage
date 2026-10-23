"""Prepare a bounded audited-source release without touching any live ledger."""
from pathlib import Path
import ast
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
STAGE = Path('D:/workspace/icim_release_20261023/strategy')
AUTO = Path('D:/workspace/icim_release_20261023/automation')
AUDIT = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'


def copy(name):
    src, target = ROOT / name, STAGE / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


pin = json.loads((AUDIT / 'delivery/capture_source_pin.json').read_text(encoding='utf-8'))
assert len(pin['files']) == 28
for name, expected in pin['files'].items():
    assert sha(ROOT / name) == expected
    copy(name)
runtime_files = {**pin['files'], 'run_ic_im_v1_4_github_digest.py': sha(ROOT / 'run_ic_im_v1_4_github_digest.py')}
copy('run_ic_im_v1_4_github_digest.py')

base = json.loads((ROOT / 'docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json').read_text(encoding='utf-8'))
tests = base['required_local_tests'] + [
    'test_ic_im_put_execution_repairs_20261016.py',
    'test_ic_im_option_maintenance_notice_20261016.py',
    'test_ic_im_put_delivery_completion_20261016.py',
    'test_ic_im_fear_source_delivery_20261016.py',
    'test_ic_im_fear_delivery_adversarial_20261016.py',
    'test_icim_ma23_market_20261023.py',
    'test_icim_ma23_clocks_20261023.py',
    'test_icim_ma23_concurrency_20261023.py',
    'test_icim_ma23_state_20261023.py',
    'test_icim_ma23_publication_guard_20261023.py',
]
assert len(tests) == len(set(tests)) == 43
# Ship imported test helpers too; collect only the declared suite, once each.
helpers, pending = set(), list(tests)
while pending:
    name = pending.pop()
    if name in helpers:
        continue
    helpers.add(name)
    source = ROOT / name
    if not source.is_file():
        raise RuntimeError('missing required test/helper: ' + name)
    copy(name)
    for node in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))):
        imported = ([a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import)
                    else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
        for value in imported:
            child = value + '.py'
            if value.startswith('test_') and (ROOT / child).is_file() and child not in helpers:
                pending.append(child)

for p in (ROOT / 'quant_research_runs/20261022_free_ic_sources_v1/probe_02/raw').iterdir():
    if p.is_file():
        copy(p.relative_to(ROOT).as_posix())
copy('quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin')
for folder in ['tests/fixtures/icim_external_review_20261021',
               'tests/fixtures/icim_external_review_round2_20261021']:
    for p in (ROOT / folder).rglob('*'):
        if p.is_file():
            copy(p.relative_to(ROOT).as_posix())

original_exclusions = json.loads((AUDIT / 'exclusions.json').read_text(encoding='utf-8'))
exclusions = [n for n in original_exclusions if n.split('::', 1)[0] in tests]
fixture_paths = [p for folder in ['quant_research_runs/20261022_free_ic_sources_v1/probe_02/raw',
                                 'tests/fixtures/icim_external_review_20261021',
                                 'tests/fixtures/icim_external_review_round2_20261021']
                 for p in (ROOT / folder).rglob('*') if p.is_file()]
fixture_paths.append(ROOT / 'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin')
manifest = {
    'schema': 'icim_audited_daily_release_v1', 'release': '20261023-audited-inputs-v1',
    'build_identity_unchanged': True, 'files': runtime_files,
    'fixture_sha256': {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(fixture_paths)},
    'required_local_tests': tests, 'required_test_helpers': sorted(helpers - set(tests)),
    'historical_exclusion_registry_sha256': sha(AUDIT / 'exclusions.json'),
    'historical_exclusion_registry': original_exclusions,
    'daily_gate_deselections': exclusions,
    'deselection_scope': 'Only the intersection of the unchanged previously accepted 22-item registry and this bounded daily suite; no new excluded nodeid.',
    'prior_full_regression': {'tests': 3121, 'deselected': 22,
        'receipt': 'quant_research_runs/20261023_multiround_adversarial_v1/cumulative_02/receipt.json'},
    'daily_gate_outer_timeout_seconds': 60,
    'calendar_years': [2022, 2023, 2024, 2025, 2026],
    'research_put_roll_candidates_adopted': False,
}
target = ROOT / 'docs/ic_im_daily_release_20261023_manifest.json'
target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
copy(target.relative_to(ROOT).as_posix())
for name in ['notes/mainline_registry.md', 'docs/ic_im_v1_4_github_gmail_runbook.md']:
    copy(name)
print(json.dumps({'production_files': len(runtime_files), 'daily_test_files': len(tests),
                  'daily_deselected_intersection': len(exclusions), 'imported_helpers': len(helpers)-len(tests)}, ensure_ascii=False))
