"""Final read-only audit checks, retaining the observed concurrent runtime exception."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import subprocess

from evidence_gate import validate_receipt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FINAL = HERE / 'accepted_final'
def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()
def bad(entries, base=ROOT):
    return [p for p, expected in entries.items() if not (base / p).is_file() or sha(base / p) != expected]

b = read(HERE / 'baseline.json')
old = read(ROOT / 'quant_research_runs/20261021_external_review_v1/baseline.json')
outputs = read(ROOT / 'quant_research_runs/20261021_external_review_redteam_v1/outputs_baseline.json')
previous = read(ROOT / 'quant_research_runs/20261021_external_review_round2_v1/final/source_after.json')
observed = read(HERE / 'runtime_concurrent_snapshot/observation.json')
bound = read(FINAL / 'source_after.json')
receipt = read(FINAL / 'receipt.json')
counts = validate_receipt(FINAL)
changed = bad(b['backed_up'])
allowed = {'poe_ic_im_mainline_v1_4_bot.py', 'ic_im_sse_contract_identity_v1.py',
           'ic_im_put_execution_v1.py', 'ic_im_v1_4_policy.py', 'poe_ic_im_v1_4_state.py',
           'test_ic_im_v1_4_integration_guards.py', 'notes/mainline_registry.md'}
runtime_exception = {'runtime/ic_im_v1_4_r1/latest.json'}
prior_backed = {p: h for p, h in previous.items() if p in b['backed_up']}
checks = dict(backup_mismatches=bad(b['backed_up'], HERE / 'backup'),
              previous_bound_backup_mismatches=bad(prior_backed, HERE / 'backup'),
              prior_evidence_mismatches=bad(b['prior_evidence']),
              original_selected_immutable_mismatches=bad(old['immutable']),
              source_binding_mismatches=bad(bound),
              unexpected_baseline_changes=sorted(set(changed) - allowed - runtime_exception),
              outputs_mismatches=bad({p: v['sha256'] for p, v in outputs['files'].items()}),
              concurrent_runtime_observation_mismatches=bad(observed['files']))
actual_outputs = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'outputs').rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts}
checks['output_path_delta'] = sorted(actual_outputs.symmetric_difference(outputs['files']))
all_runtime = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'runtime').rglob('*')
               if p.is_file() and p.suffix in {'.md', '.json', '.sha256'}}
old_runtime = {p for p in b['backed_up'] if p.startswith('runtime/')}
new_runtime = sorted(all_runtime - old_runtime)
checks['unexpected_new_runtime'] = sorted(set(new_runtime) - set(observed['files']))
latest = read(ROOT / 'runtime/ic_im_v1_4_r1/latest.json')
before_head = read(HERE / 'backup/runtime/ic_im_v1_4_r1/latest.json')
payload = {k: v for k, v in latest.items() if k != 'digest'}
canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
runtime_link_ok = (latest['sequence'] == before_head['sequence'] + 1
                   and latest['previous_digest'] == before_head['digest']
                   and hashlib.sha256(canonical).hexdigest() == latest['digest'])
checks['runtime_link_errors'] = [] if runtime_link_ok else ['observed runtime does not link/hash correctly']
spec = ROOT / 'docs/ic_im_external_review_round2_redteam_20261021_v1_spec.md'
checks['audit_spec_errors'] = [] if sha(spec) == spec.with_suffix('.md.sha256').read_text().split()[0] else [spec.name]
test_run = read(FINAL / 'test_run.json')
test_files = [p for p in test_run['files'] if p.endswith('.py') and '::' not in p]
compiled = sorted((allowed - {'notes/mainline_registry.md'}) |
                  {p.name for p in ROOT.glob('test_icim_round2_audit_*_20261021.py')})
for relative in compiled:
    compile((ROOT / relative).read_bytes(), relative, 'exec')
for path in HERE.glob('*.py'):
    compile(path.read_bytes(), path.name, 'exec')
patch = []
for relative in sorted(set(changed) & allowed):
    patch += difflib.unified_diff((HERE / 'backup' / relative).read_text(encoding='utf-8').splitlines(True),
                                 (ROOT / relative).read_text(encoding='utf-8').splitlines(True),
                                 fromfile='before/' + relative, tofile='after/' + relative)
added = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob('test_icim_round2_audit_*_20261021.py'))
added += sorted(p.relative_to(ROOT).as_posix() for p in HERE.glob('*.py'))
for relative in added:
    patch += difflib.unified_diff([], (ROOT / relative).read_text(encoding='utf-8').splitlines(True),
                                 fromfile='/dev/null', tofile='after/' + relative)
(HERE / 'source_delta.patch').write_bytes(''.join(patch).encode('utf-8'))
git_check = subprocess.run(['git', '-c', 'safe.directory=' + ROOT.as_posix(), 'diff', '--check'],
                           cwd=ROOT, text=True, capture_output=True)
result = dict(verified_at=datetime.now(timezone.utc).isoformat(),
              passed_with_documented_runtime_exception=not any(checks.values()) and git_check.returncode == 0,
              checks=checks, changed_baseline_files=changed, allowed_audit_source_changes=sorted(allowed),
              baseline_backups=len(b['backed_up']), prior_evidence_protected=len(b['prior_evidence']),
              previous_source_receipt_entries_in_backup=len(prior_backed),
              selected_original_immutable=len(old['immutable']),
              prospective_outputs=len(outputs['files']), prospective_outputs_scope=outputs['scope'],
              concurrent_runtime_exception=observed, new_runtime_paths=new_runtime,
              final_receipt='accepted_final/receipt.json', receipt_sha256=sha(FINAL / 'receipt.json'),
              counts=counts, test_files=len(test_files), source_files=receipt['source_files'],
              deselected=receipt['deselected'], compiled_files=compiled,
              git_diff_check_exit=git_check.returncode, git_diff_check_output=git_check.stdout + git_check.stderr,
              source_delta_sha256=sha(HERE / 'source_delta.patch'),
              final_source_sha256={p: sha(ROOT / p) for p in sorted(allowed) if p != 'notes/mainline_registry.md'})
target = HERE / 'final_verification.json'
if target.exists():
    raise SystemExit('Refusing to overwrite final verification')
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result['passed_with_documented_runtime_exception'] else 1)
