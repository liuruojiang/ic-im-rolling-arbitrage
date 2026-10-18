"""Read-only scope and saved-data checks; no network, ledger writes or sends."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / 'cloud/scripts'))
sys.path.insert(0, str(ROOT))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

old_run = ROOT / 'quant_research_runs/20261016_put_execution_repairs_v1'
guard = json.loads((old_run / 'backup_manifest.json').read_text(encoding='utf-8'))['guarded']
changed_guard = [p for p, expected in guard.items() if sha(ROOT / p) != expected]
assert not changed_guard, changed_guard

def defs(path):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(
        ast.parse(path.read_text(encoding='utf-8'))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

changes = {}
for name, allowed in [('poe_ic_im_mainline_v1_4_bot.py', {'_handle_signal'}),
                      ('run_ic_im_v1_4_github_digest.py', {'render_stored_close_report', 'build_artifacts'})]:
    before, after = defs(HERE/'backups'/name), defs(ROOT/name)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert changed == allowed, changed
    changes[name] = sorted(changed)

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

old = module('old_digest', HERE/'backups/cloud_build_ic_im_v1_4_digest.py')
new = module('new_digest', HERE/'cloud/scripts/build_ic_im_v1_4_digest.py')
fixture_path = HERE/'cloud/tests/fixtures/icim/readiness_20261009/result.json'
saved = json.loads(fixture_path.read_text(encoding='utf-8'))
assert old.build_success(saved, '', '') == new.build_success(saved, '', '')
assert old.build_success_html(saved, '') == new.build_success_html(saved, '')
assert sha(ROOT/'ic_im_option_maintenance_notice.py') == sha(HERE/'cloud/scripts/ic_im_option_maintenance_notice.py')

result = dict(guarded_files_unchanged=len(guard), changed_definitions=changes,
              real_saved_report_plain_html_byte_equal=True, real_saved_fixture=str(fixture_path.relative_to(ROOT)),
              helper_identical_local_cloud=True,
              previous_research_files={str(p.relative_to(ROOT)): sha(p) for p in old_run.iterdir() if p.is_file()},
              source_sha256={str(p.relative_to(ROOT)): sha(p) for p in [
                  ROOT/'ic_im_option_maintenance_notice.py', ROOT/'poe_ic_im_mainline_v1_4_bot.py',
                  ROOT/'run_ic_im_v1_4_github_digest.py', ROOT/'test_ic_im_option_maintenance_notice_20261016.py',
                  HERE/'cloud/scripts/build_ic_im_v1_4_digest.py',
                  HERE/'cloud/tests/test_ic_im_v1_4_monthly_maintenance_notice.py']})
(HERE/'independent_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in {'source_sha256','previous_research_files'}}, ensure_ascii=False))
