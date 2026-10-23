"""Create a new source identity without replacing the prior frozen release."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'
NEW_TESTS = [f'test_icim_redteam_{part}_repairs_20261023.py' for part in ['strategy', 'market', 'evidence', 'server']]


def main():
    value = json.loads((ROOT / 'docs/ic_im_daily_release_20261023_manifest.json').read_text('utf-8'))
    value['release'] = '20261023-redteam-repairs-v3-r3'
    value['build_identity_unchanged'] = False
    value['supersedes_local_gate_manifest'] = 'docs/ic_im_daily_release_20261023_manifest.json'
    value['repair_spec'] = 'docs/ic_im_redteam_repairs_20261023_v3_spec.md'
    value['files'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in value['files']}
    value['required_local_tests'] += NEW_TESTS
    fixtures = list((ROOT / 'tests/fixtures/icim_verified_ledger_20261021').rglob('*')) + list(
        (ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle').rglob('*')) + [
        ROOT / 'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin',
        ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle/blobs/7155726823743f87af08fae1ce90ef44b6565ef8589cb71199194f8540e42453.bin',
    ]
    value['fixture_sha256'].update({p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in fixtures if p.is_file()})
    # Bind transitive local test helpers as well as the original gate helpers.
    helpers = set(value['required_test_helpers'])
    pending = list(NEW_TESTS)
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((ROOT / name).read_text('utf-8-sig'))):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in modules:
                dependency = module.split('.')[0] + '.py'
                if dependency.startswith('test_') and (ROOT / dependency).is_file():
                    if dependency not in value['required_local_tests']:
                        helpers.add(dependency)
                    pending.append(dependency)
    value['required_test_helpers'] = sorted(helpers)
    with TARGET.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(TARGET)


if __name__ == '__main__':
    main()
